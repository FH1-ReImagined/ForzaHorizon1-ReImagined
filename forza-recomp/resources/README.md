# Windows executable icon

`forza.ico` is the ReImagined logo mark (`docs/images/logo-mark.png`, the website's
`images/logo-mark.png`), centred on a square with a little margin and packaged at 16, 20, 24, 32,
40, 48, 64, 128 and 256 pixels with transparency. The game, PyInstaller launcher and Inno Setup
installer share it. Keeping it inside `forza-recomp` also includes it in the launcher's source
bundle and build hash.

To regenerate from the repository root with Pillow installed:

```sh
python -c "from PIL import Image; m = Image.open('docs/images/logo-mark.png').convert('RGBA'); s = round(max(m.size) * 1.04); c = Image.new('RGBA', (s, s)); c.paste(m, ((s - m.width) // 2, (s - m.height) // 2), m); c.resize((256, 256), Image.LANCZOS).save('forza-recomp/resources/forza.ico', sizes=[(n, n) for n in (16, 20, 24, 32, 40, 48, 64, 128, 256)])"
```

The Windows CMake target compiles `app.rc.in` for both base-game and TU4 builds.
Linux builds do not compile Windows resources.
