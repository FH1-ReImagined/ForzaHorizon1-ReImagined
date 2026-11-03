// The HLSL compiler, from d3dcompiler_47.dll beside the game executable when there is one (we ship Microsoft's, from the
// Windows SDK's Redist folder), else the system's. Under Wine and Proton the system copy is Wine's own
// reimplementation, which fails on our translated shaders (large SM 5.1 programs with unbounded texture
// arrays): no game shader compiled, a black screen with the game running behind it. ReGPU defines the two
// functions it uses here instead of linking d3dcompiler.lib, so every call goes to the copy loaded below.
#include <windows.h>
#include <d3dcompiler.h>
#include <string>
#include "log.h"

namespace
{
HMODULE Library()
{
    static const HMODULE module = [] {
        wchar_t path[MAX_PATH];
        const DWORD n = GetModuleFileNameW(nullptr, path, MAX_PATH);
        HMODULE m = nullptr;
        if (n && n < MAX_PATH)
        {
            std::wstring local(path, n);
            local = local.substr(0, local.find_last_of(L"\\/") + 1) + L"d3dcompiler_47.dll";
            // A full path loads that file even where Wine would substitute its builtin for the bare name.
            m = LoadLibraryExW(local.c_str(), nullptr, LOAD_WITH_ALTERED_SEARCH_PATH);
        }
        Log("d3dcompiler: %s", m ? "using d3dcompiler_47.dll beside the executable" : "using the system's d3dcompiler_47.dll");
        if (!m)
            m = LoadLibraryW(L"d3dcompiler_47.dll");
        if (!m)
            Log("d3dcompiler: d3dcompiler_47.dll not found: no shader can be compiled");
        return m;
    }();
    return module;
}

template <typename F>
F Function(const char* name)
{
    HMODULE m = Library();
    return m ? reinterpret_cast<F>(GetProcAddress(m, name)) : nullptr;
}
} // namespace

HRESULT WINAPI D3DCompile(LPCVOID src, SIZE_T size, LPCSTR name, const D3D_SHADER_MACRO* defines, ID3DInclude* include, LPCSTR entry,
    LPCSTR target, UINT flags1, UINT flags2, ID3DBlob** code, ID3DBlob** errors)
{
    static const auto f = Function<pD3DCompile>("D3DCompile");
    if (errors)
        *errors = nullptr;
    return f ? f(src, size, name, defines, include, entry, target, flags1, flags2, code, errors) : E_NOTIMPL;
}

HRESULT WINAPI D3DCreateBlob(SIZE_T size, ID3DBlob** blob)
{
    using CreateBlob = HRESULT(WINAPI*)(SIZE_T, ID3DBlob**);
    static const auto f = Function<CreateBlob>("D3DCreateBlob");
    return f ? f(size, blob) : E_NOTIMPL;
}
