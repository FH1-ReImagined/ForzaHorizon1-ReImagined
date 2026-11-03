// xenos_dis [--hlsl] <shader.bin>...: disassemble captured Xenos microcode
// (vs_*.bin / ps_*.bin from REGPU_DUMP_SHADERS); --hash prints each file with a hash of its
// translated HLSL (no compile: shaders with the same hash share bytecode); --hlsl translates and
// compiles each one instead and reports the result.
#include <cstdio>
#include <cstring>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>

#include <d3dcompiler.h>
#include <windows.h>

#include "gpu/hlsl.h"
#include "gpu/ucode.h"

int main(int argc, char** argv)
{
    bool hlsl = false, print = false, hashOnly = false;
    int failures = 0;
    for (int i = 1; i < argc; i++)
    {
        if (!strcmp(argv[i], "--hlsl"))
        {
            hlsl = true;
            continue;
        }
        if (!strcmp(argv[i], "--hash"))
        {
            hlsl = hashOnly = true;
            continue;
        }
        if (!strcmp(argv[i], "--print"))
        {
            print = true;
            continue;
        }
        std::ifstream f(argv[i], std::ios::binary);
        std::vector<char> bytes{ std::istreambuf_iterator<char>(f), {} };
        const std::string name = argv[i];
        const bool pixel = name.find("ps_") != std::string::npos;
        const auto shader = ucode::Parse(reinterpret_cast<const uint32_t*>(bytes.data()), uint32_t(bytes.size() / 4), pixel);
        if (!hlsl)
        {
            printf("=== %s\n%s\n", argv[i], ucode::Disassemble(shader).c_str());
            continue;
        }
        const TranslatedShader t = TranslateShader(shader);
        if (!t.ok)
        {
            printf("FAIL translate %s: %s\n", argv[i], t.error.c_str());
            failures++;
            continue;
        }
        if (hashOnly)
        {
            // The renderer's fast path for patched copies must give the same fetch layout.
            TranslatedShader layout;
            const bool dynamic = !pixel && TranslateVertexFetchLayout(shader, layout);
            if (dynamic && (layout.vertexFetchLayout != t.vertexFetchLayout || layout.vertexFetchConstants != t.vertexFetchConstants))
                printf("LAYOUT MISMATCH %s\n", argv[i]);
            if (!pixel && !dynamic)
                printf("BAKED FETCH %s\n", argv[i]); // some fetch kept literal fields in the HLSL
            uint64_t h = 0xCBF29CE484222325ull;
            for (char c : t.hlsl)
                h = (h ^ uint8_t(c)) * 0x100000001B3ull;
            // The renderer's canonical key (CanonicalKey): microcode with the patched vertex
            // fetch fields masked. Shaders with equal keys must have equal HLSL hashes.
            std::vector<uint32_t> w = shader.words;
            for (const ucode::Cf& cf : shader.cf)
            {
                if (cf.IsExec())
                    for (uint32_t k = 0; k < cf.count(); k++)
                    {
                        const uint32_t index = cf.address() + k;
                        if (!((cf.sequence() >> (2 * k)) & 1) || (index + 1) * 3 > w.size() || (w[index * 3] & 0x1F) != 0)
                            continue;
                        w[index * 3] &= ~0x1FF00000u;
                        w[index * 3 + 1] &= 0x80000000u;
                        w[index * 3 + 2] &= 0x80000000u;
                    }
                if (cf.IsEnd())
                    break;
            }
            uint64_t canon = 0xCBF29CE484222325ull;
            for (uint32_t x : w)
                for (int b = 0; b < 4; b++)
                    canon = (canon ^ ((x >> (8 * b)) & 0xFF)) * 0x100000001B3ull;
            printf("%s %016llx canon %016llx\n", argv[i], (unsigned long long)h, (unsigned long long)canon);
            if (print)
                printf("%s\n", t.hlsl.c_str());
            continue;
        }
        if (print)
            printf("%s\n", t.hlsl.c_str());
        ID3DBlob* code = nullptr;
        ID3DBlob* errors = nullptr;
        const D3D_SHADER_MACRO vertexStage[] = { { "REGPU_VERTEX_STAGE", "1" }, { nullptr, nullptr } };
        const HRESULT hr = D3DCompile(t.hlsl.data(), t.hlsl.size(), argv[i], pixel ? nullptr : vertexStage, nullptr, "main", pixel ? "ps_5_1" : "vs_5_1",
            D3DCOMPILE_OPTIMIZATION_LEVEL3 | D3DCOMPILE_ENABLE_UNBOUNDED_DESCRIPTOR_TABLES, 0, &code, &errors);
        if (FAILED(hr))
        {
            printf("FAIL compile %s:\n%s\n", argv[i], errors ? static_cast<const char*>(errors->GetBufferPointer()) : "?");
            failures++;
        }
        else
            printf("ok   %s (%zu bytes DXBC)\n", argv[i], code->GetBufferSize());
    }
    return failures;
}
