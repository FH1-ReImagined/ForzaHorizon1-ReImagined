#include "gpu/ucode.h"

#include <cstdio>

namespace ucode
{
namespace
{
const char* kVectorOps[32] = { "add", "mul", "max", "min", "seq", "sgt", "sge", "sne", "frc", "trunc", "floor", "mad", "cndeq",
    "cndge", "cndgt", "dp4", "dp3", "dp2add", "cube", "max4", "setp_eq_push", "setp_ne_push", "setp_gt_push", "setp_ge_push",
    "kill_eq", "kill_gt", "kill_ge", "kill_ne", "dst", "maxa", "?30", "?31" };

const char* kScalarOps[64] = { "adds", "adds_prev", "muls", "muls_prev", "muls_prev2", "maxs", "mins", "seqs", "sgts", "sges",
    "snes", "frcs", "truncs", "floors", "exp", "logc", "log", "rcpc", "rcpf", "rcp", "rsqc", "rsqf", "rsq", "maxas", "maxasf",
    "subs", "subs_prev", "setp_eq", "setp_ne", "setp_gt", "setp_ge", "setp_inv", "setp_pop", "setp_clr", "setp_rstr",
    "kills_eq", "kills_gt", "kills_ge", "kills_ne", "kills_one", "sqrt", "?41", "mulsc0", "mulsc1", "addsc0", "addsc1",
    "subsc0", "subsc1", "sin", "cos", "retain_prev", "?51", "?52", "?53", "?54", "?55", "?56", "?57", "?58", "?59", "?60",
    "?61", "?62", "?63" };

const char* kCfOps[16] = { "nop", "exec", "exece", "cexec", "cexece", "cexec_pred", "cexece_pred", "loop_start", "loop_end",
    "ccall", "ret", "cjmp", "alloc", "cexec_pred_clean", "cexece_pred_clean", "vfetch_end" };

uint32_t Bits(uint32_t v, int lo, int n) { return (v >> lo) & ((1u << n) - 1); }

Source DecodeSource(uint32_t reg, bool isTemp, uint32_t swizzle, bool negate, bool absConstants)
{
    Source s{};
    s.isConstant = !isTemp;
    s.negate = negate;
    if (isTemp)
    {
        s.reg = reg & 0x3F;
        s.abs = (reg & 0x80) != 0;
    }
    else
    {
        s.reg = reg;
        s.abs = absConstants;
    }
    for (int c = 0; c < 4; c++)
        s.swizzle[c] = uint8_t(((swizzle >> (2 * c)) + c) & 3);
    return s;
}

std::string SourceText(const Source& s)
{
    static const char comp[] = "xyzw";
    char buf[48];
    snprintf(buf, sizeof(buf), "%s%s%s%u.%c%c%c%c%s", s.negate ? "-" : "", s.abs ? "|" : "", s.isConstant ? "c" : "r", s.reg,
        comp[s.swizzle[0]], comp[s.swizzle[1]], comp[s.swizzle[2]], comp[s.swizzle[3]], s.abs ? "|" : "");
    return buf;
}

std::string MaskText(uint32_t mask)
{
    std::string m;
    for (int c = 0; c < 4; c++)
        m += (mask >> c) & 1 ? "xyzw"[c] : '_';
    return m;
}

std::string DestText(const Shader& shader, uint32_t reg, bool exported)
{
    char buf[24];
    if (!exported)
        snprintf(buf, sizeof(buf), "r%u", reg);
    else if (shader.pixel)
        snprintf(buf, sizeof(buf), reg == 61 ? "oDepth" : "oC%u", reg);
    else if (reg == 62)
        snprintf(buf, sizeof(buf), "oPos");
    else if (reg == 63)
        snprintf(buf, sizeof(buf), "oPts");
    else if (reg >= 32)
        snprintf(buf, sizeof(buf), "eM%u", reg - 32);
    else
        snprintf(buf, sizeof(buf), "o%u", reg);
    return buf;
}
}

bool Cf::IsExec() const
{
    switch (op())
    {
    case CfOp::Exec:
    case CfOp::ExecEnd:
    case CfOp::CondExec:
    case CfOp::CondExecEnd:
    case CfOp::CondExecPred:
    case CfOp::CondExecPredEnd:
    case CfOp::CondExecPredClean:
    case CfOp::CondExecPredCleanEnd:
        return true;
    default:
        return false;
    }
}

bool Cf::IsEnd() const
{
    return op() == CfOp::ExecEnd || op() == CfOp::CondExecEnd || op() == CfOp::CondExecPredEnd || op() == CfOp::CondExecPredCleanEnd;
}

uint32_t UsedDwords(const uint32_t* microcodeBE, uint32_t dwords)
{
    const Shader s = Parse(microcodeBE, dwords, false);
    if (!s.valid)
        return dwords;
    uint32_t slots = 0;
    for (const Cf& cf : s.cf)
    {
        if (cf.op() == CfOp::CondCall || cf.op() == CfOp::CondJmp || cf.op() == CfOp::Return)
            return dwords;
        if (cf.IsExec() && cf.count())
            slots = std::max(slots, cf.address() + cf.count());
    }
    return slots && slots * 3 <= dwords ? slots * 3 : dwords;
}

const char* VectorOpName(uint32_t op) { return kVectorOps[op & 31]; }
const char* ScalarOpName(uint32_t op) { return kScalarOps[op & 63]; }

bool Plausible(const Shader& shader)
{
    if (!shader.valid)
        return false;
    for (const Cf& cf : shader.cf)
    {
        if (!cf.IsExec())
            continue;
        for (uint32_t k = 0; k < cf.count(); k++)
        {
            const uint32_t index = cf.address() + k;
            if (index * 3 + 2 >= shader.words.size())
                return false;
            if ((cf.sequence() >> (2 * k)) & 1)
                continue; // vertex fetch: no opcode to check
            const Alu a = DecodeAlu(&shader.words[index * 3]);
            if (kVectorOps[a.vectorOp][0] == '?' || kScalarOps[a.scalarOp][0] == '?')
                return false;
        }
    }
    return true;
}

Shader Parse(const uint32_t* microcodeBE, uint32_t dwords, bool pixel)
{
    Shader shader;
    shader.pixel = pixel;
    shader.words.resize(dwords);
    for (uint32_t i = 0; i < dwords; i++)
        shader.words[i] = __builtin_bswap32(microcodeBE[i]);

    // Control flow comes first, two 48-bit instructions per 3 dwords, up to
    // the lowest instruction address any exec references.
    uint32_t cfEnd = dwords / 3;
    for (uint32_t triple = 0; triple < cfEnd && triple * 3 + 2 < dwords; triple++)
    {
        const uint32_t* w = &shader.words[triple * 3];
        Cf pair[2];
        pair[0].lo = w[0];
        pair[0].hi = w[1] & 0xFFFF;
        pair[1].lo = (w[1] >> 16) | (w[2] << 16);
        pair[1].hi = w[2] >> 16;
        bool ended = false;
        for (const Cf& cf : pair)
        {
            shader.cf.push_back(cf);
            if (cf.IsExec() && cf.count())
                cfEnd = std::min(cfEnd, cf.address());
            if (cf.IsEnd())
                ended = true;
        }
        if (ended)
        {
            shader.valid = true;
            // Subroutines live after the end: while a call targets an instruction not parsed yet, the
            // control flow area is read on (up to the first exec's code). Programs without calls stop
            // at the end as before. From the FM4 port.
            const auto unparsedCall = [&] {
                for (const Cf& c : shader.cf)
                    if (c.op() == CfOp::CondCall && (c.lo & 0x1FFF) >= shader.cf.size())
                        return true;
                return false;
            };
            for (triple++; unparsedCall() && triple < cfEnd && triple * 3 + 2 < dwords; triple++)
            {
                const uint32_t* v = &shader.words[triple * 3];
                Cf more[2];
                more[0].lo = v[0];
                more[0].hi = v[1] & 0xFFFF;
                more[1].lo = (v[1] >> 16) | (v[2] << 16);
                more[1].hi = v[2] >> 16;
                for (const Cf& cf : more)
                {
                    shader.cf.push_back(cf);
                    if (cf.IsExec() && cf.count())
                        cfEnd = std::min(cfEnd, cf.address());
                }
            }
            break;
        }
    }
    return shader;
}

Alu DecodeAlu(const uint32_t* w)
{
    Alu a{};
    a.vectorDest = Bits(w[0], 0, 6);
    const bool absConstants = Bits(w[0], 7, 1);
    a.scalarDest = Bits(w[0], 8, 6);
    a.scalarDestRelative = Bits(w[0], 14, 1);
    a.exportData = Bits(w[0], 15, 1);
    a.vectorMask = Bits(w[0], 16, 4);
    a.scalarMask = Bits(w[0], 20, 4);
    a.vectorClamp = Bits(w[0], 24, 1);
    a.scalarClamp = Bits(w[0], 25, 1);
    a.scalarOp = Bits(w[0], 26, 6);
    const uint32_t swz[3] = { Bits(w[1], 16, 8), Bits(w[1], 8, 8), Bits(w[1], 0, 8) };
    const bool neg[3] = { bool(Bits(w[1], 26, 1)), bool(Bits(w[1], 25, 1)), bool(Bits(w[1], 24, 1)) };
    a.predCondition = Bits(w[1], 27, 1);
    a.predicated = Bits(w[1], 28, 1);
    const uint32_t reg[3] = { Bits(w[2], 16, 8), Bits(w[2], 8, 8), Bits(w[2], 0, 8) };
    a.vectorOp = Bits(w[2], 24, 5);
    const bool temp[3] = { bool(Bits(w[2], 31, 1)), bool(Bits(w[2], 30, 1)), bool(Bits(w[2], 29, 1)) };
    // Constant sources take the const_0 / const_1 relative bits in order;
    // address_absolute picks a0 over the loop index.
    const bool addressAbsolute = Bits(w[1], 29, 1);
    const bool constRelative[2] = { bool(Bits(w[1], 31, 1)), bool(Bits(w[1], 30, 1)) };
    // Operands in slot order: the vector op's (by arity), then src3 for the scalar op.
    const uint32_t op = Bits(w[2], 24, 5);
    const uint32_t vectorArity = (op == 11 || op == 12 || op == 13 || op == 14 || op == 17) ? 3
        : (op == 8 || op == 9 || op == 10 || op == 19) ? 1 : 2;
    uint32_t constSlot = 0;
    for (int i = 0; i < 3; i++)
    {
        a.src[i] = DecodeSource(reg[i], temp[i], swz[i], neg[i], absConstants);
        const bool used = uint32_t(i) < vectorArity || i == 2;
        if (used && !temp[i] && constSlot < 2)
        {
            if (constRelative[constSlot])
                a.src[i].relative = addressAbsolute ? 1 : 2;
            constSlot++;
        }
    }
    a.src3RawReg = reg[2];
    a.src3IsTemp = temp[2];
    a.src3RawSwizzle = swz[2];
    return a;
}

Fetch DecodeFetch(const uint32_t* w)
{
    Fetch f{};
    f.opcode = Bits(w[0], 0, 5);
    f.src = Bits(w[0], 5, 6);
    f.dest = Bits(w[0], 12, 6);
    f.destSwizzle = Bits(w[1], 0, 12);
    f.predicated = Bits(w[1], 31, 1);
    f.predCondition = Bits(w[2], 31, 1);
    if (f.opcode == 0)
    {
        f.constIndex = Bits(w[0], 20, 5) * 3 + Bits(w[0], 25, 2);
        f.srcSwizzle = Bits(w[0], 30, 2);
        f.isSigned = Bits(w[1], 12, 1);
        f.isNormalized = Bits(w[1], 13, 1) == 0;
        f.format = Bits(w[1], 16, 6);
        f.isMini = Bits(w[1], 30, 1);
        f.roundIndex = Bits(w[1], 15, 1);
        f.expAdjust = int32_t(Bits(w[1], 24, 6) << 26) >> 26;
        f.stride = Bits(w[2], 0, 8);
        f.offset = Bits(w[2], 8, 23);
    }
    else
    {
        f.constIndex = Bits(w[0], 20, 5);
        f.denormalized = Bits(w[0], 25, 1);
        f.srcSwizzle = Bits(w[0], 26, 6);
        f.dimension = Bits(w[2], 14, 2);
        f.magFilter = Bits(w[1], 12, 2);
        f.lodBias = float(int32_t(Bits(w[2], 2, 7) << 25) >> 25) / 16.0f;
        f.offsetX = float(int32_t(Bits(w[2], 16, 5) << 27) >> 27) * 0.5f;
        f.offsetY = float(int32_t(Bits(w[2], 21, 5) << 27) >> 27) * 0.5f;
    }
    return f;
}

namespace
{
std::string FetchSwizzleText(uint32_t swz)
{
    std::string s;
    for (int c = 0; c < 4; c++)
        s += "xyzw01?_"[(swz >> (3 * c)) & 7];
    return s;
}
}

std::string Disassemble(const Shader& shader)
{
    std::string out;
    char line[256];
    snprintf(line, sizeof(line), "; %s shader, %zu dwords, %s\n", shader.pixel ? "pixel" : "vertex", shader.words.size(),
        shader.valid ? "ok" : "NO END FOUND");
    out += line;
    for (size_t i = 0; i < shader.cf.size(); i++)
    {
        const Cf& cf = shader.cf[i];
        if (cf.op() == CfOp::Nop)
            continue;
        if (cf.op() == CfOp::Alloc)
        {
            static const char* types[] = { "none", "position", "interpolators", "memory" };
            snprintf(line, sizeof(line), "%2zu  alloc %s, %u\n", i, types[cf.allocType()], cf.allocSize() + 1);
            out += line;
            continue;
        }
        if (!cf.IsExec())
        {
            snprintf(line, sizeof(line), "%2zu  %s  (raw %04X%08X)\n", i, kCfOps[unsigned(cf.op())], cf.hi, cf.lo);
            out += line;
            continue;
        }
        if (cf.op() == CfOp::CondExec || cf.op() == CfOp::CondExecEnd)
            snprintf(line, sizeof(line), "%2zu  %s b%u==%u  addr %u count %u\n", i, kCfOps[unsigned(cf.op())], (cf.hi >> 2) & 0xFF,
                cf.condition(), cf.address(), cf.count());
        else
            snprintf(line, sizeof(line), "%2zu  %s  addr %u count %u\n", i, kCfOps[unsigned(cf.op())], cf.address(), cf.count());
        out += line;
        for (uint32_t k = 0; k < cf.count(); k++)
        {
            const uint32_t index = cf.address() + k;
            if ((index + 1) * 3 > shader.words.size())
            {
                out += "      <out of range>\n";
                break;
            }
            const uint32_t* w = &shader.words[index * 3];
            const bool isFetch = (cf.sequence() >> (2 * k)) & 1;
            if (isFetch)
            {
                const Fetch f = DecodeFetch(w);
                if (f.opcode == 0)
                    snprintf(line, sizeof(line), "      vfetch r%u.%s, r%u, vf%u  fmt %u stride %u offset %u%s%s\n", f.dest,
                        FetchSwizzleText(f.destSwizzle).c_str(), f.src, f.constIndex, f.format, f.stride, f.offset,
                        f.isSigned ? " signed" : "", f.isMini ? " mini" : "");
                else
                    snprintf(line, sizeof(line), "      tfetch%s r%u.%s, r%u, tf%u  (op %u)\n", f.dimension == 1 ? "2D" : "", f.dest,
                        FetchSwizzleText(f.destSwizzle).c_str(), f.src, f.constIndex, f.opcode);
                out += line;
                continue;
            }
            const Alu a = DecodeAlu(w);
            const char* pred = a.predicated ? (a.predCondition ? "(p) " : "(!p) ") : "";
            if (a.vectorMask || a.vectorOp == 29 || a.vectorOp >= 20)
            {
                snprintf(line, sizeof(line), "      %s%s %s.%s = %s, %s%s%s\n", pred, kVectorOps[a.vectorOp],
                    DestText(shader, a.vectorDest, a.exportData).c_str(), MaskText(a.vectorMask).c_str(), SourceText(a.src[0]).c_str(),
                    SourceText(a.src[1]).c_str(), a.vectorOp == 11 || a.vectorOp == 12 || a.vectorOp == 13 || a.vectorOp == 14 ? ", " : "",
                    a.vectorOp == 11 || a.vectorOp == 12 || a.vectorOp == 13 || a.vectorOp == 14 ? SourceText(a.src[2]).c_str() : "");
                out += line;
            }
            if (a.scalarMask || a.scalarOp >= 27)
            {
                snprintf(line, sizeof(line), "      %s+ %s %s.%s = %s\n", pred, kScalarOps[a.scalarOp],
                    DestText(shader, a.exportData ? a.vectorDest : a.scalarDest, a.exportData).c_str(), MaskText(a.scalarMask).c_str(),
                    SourceText(a.src[2]).c_str());
                out += line;
            }
        }
    }
    return out;
}
}
