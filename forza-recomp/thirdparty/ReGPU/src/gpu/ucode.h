#pragma once
// Xenos shader microcode: decoding into a structured form, and a
// disassembler. Bit layouts follow the hardware as documented in Xenia's
// ucode.h (used as reference).
#include <cstdint>
#include <string>
#include <vector>

namespace ucode
{
enum class CfOp : uint32_t
{
    Nop = 0, Exec = 1, ExecEnd = 2, CondExec = 3, CondExecEnd = 4, CondExecPred = 5, CondExecPredEnd = 6,
    LoopStart = 7, LoopEnd = 8, CondCall = 9, Return = 10, CondJmp = 11, Alloc = 12,
    CondExecPredClean = 13, CondExecPredCleanEnd = 14, MarkVsFetchDone = 15,
};

struct Cf
{
    uint32_t lo = 0, hi = 0; // 48 bits
    CfOp op() const { return CfOp((hi >> 12) & 0xF); }
    // Exec family.
    uint32_t address() const { return lo & 0xFFF; }
    uint32_t count() const { return (lo >> 12) & 7; }
    uint32_t sequence() const { return (lo >> 16) & 0xFFF; }
    // Cond exec: bool constant index and condition.
    uint32_t boolAddress() const { return (lo >> 16) & 0xFF; } // for CondExec: bits 16-23 of word 0
    bool condition() const { return (hi >> 10) & 1; }
    // Alloc.
    uint32_t allocSize() const { return lo & 7; }
    uint32_t allocType() const { return (hi >> 9) & 3; }
    bool IsExec() const;
    bool IsEnd() const;
};

struct Source
{
    uint32_t reg;     // register or constant index
    bool isConstant;
    bool negate;
    bool abs;
    uint8_t swizzle[4]; // component per channel, 0-3
    uint8_t relative;   // constants: 0 absolute, 1 + a0, 2 + aL (loop index)
};

struct Alu
{
    uint32_t vectorOp, scalarOp;
    uint32_t vectorDest, scalarDest;
    uint32_t vectorMask, scalarMask;
    bool exportData;
    bool scalarDestRelative;
    uint32_t src3RawReg; // for the constant/temp scalar ops (mulsc etc.)
    bool src3IsTemp;
    uint32_t src3RawSwizzle;
    bool vectorClamp, scalarClamp;
    bool predicated, predCondition;
    Source src[3];
};

struct Fetch
{
    uint32_t opcode; // 0 vertex, 1 texture, other: texture queries
    uint32_t dest, src;
    uint32_t destSwizzle; // 12 bits: 3 per channel
    uint32_t constIndex;  // vertex: fetch constant (index*3+sel); texture: slot
    // Vertex fetch.
    uint32_t format, stride, offset;
    bool isSigned, isNormalized, isMini;
    int32_t expAdjust;   // result scaled by 2^expAdjust
    bool roundIndex;     // index rounded to nearest instead of truncated
    // Texture fetch.
    uint32_t srcSwizzle; // 6 bits for texture (3 x 2)
    bool denormalized;   // coordinates in texels
    uint32_t dimension;
    float lodBias;       // texture fetch LOD bias (instruction)
    uint32_t magFilter = 3; // texture fetch: 0 point, 1 linear, 3 the fetch constant's (w1 bits 12-13)
    float offsetX = 0, offsetY = 0; // texture fetch texel offsets (signed 5 bits, half texels)
    bool predicated, predCondition;
};

struct Instruction
{
    bool isFetch;
    Alu alu;
    Fetch fetch;
};

struct Shader
{
    bool pixel = false;
    std::vector<Cf> cf;
    std::vector<uint32_t> words; // host-order dwords
    bool valid = false;
};

Shader Parse(const uint32_t* microcodeBE, uint32_t dwords, bool pixel);
// Dwords up to the last instruction slot the program uses. Loaded shaders carry a
// per-load stamp after that, so hashes cover only this much. Returns dwords when the
// control flow has calls or jumps or doesn't parse.
uint32_t UsedDwords(const uint32_t* microcodeBE, uint32_t dwords);
// Whether the microcode can plausibly be a shader at all: valid control flow, and every ALU
// instruction using an opcode the ISA defines. The GPU lookahead predicts shader loads from
// the ring buffer before the guest (or the command processor) has finished writing that
// memory, and translating those predicts a shader that never exists: it costs a background
// compile and logs a failure for a garbage hash.
bool Plausible(const Shader& shader);
Alu DecodeAlu(const uint32_t* words);
Fetch DecodeFetch(const uint32_t* words);
std::string Disassemble(const Shader& shader);
const char* VectorOpName(uint32_t op);
const char* ScalarOpName(uint32_t op);
}
