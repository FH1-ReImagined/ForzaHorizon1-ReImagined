"""Texture packs (#384): the packs in the textures folder (add, remove, turn on or off, order),
Make HD pack, which upscales the player's own dump on this PC (hd_pack.py), and Compress PNGs,
which makes a pack's PNGs DDS files that take a quarter of the graphics memory (#413,
texture_compress.py)."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QProgressBar, QPushButton, QVBoxLayout

from .. import GITHUB_URL, hd_pack, installer, pins, schema, specs, texture_compress, texture_packs
from .mods_page import FolderListPage, _link_label, _size
from .widgets import Switch, card, label
from . import theme

DOCS_URL = f"{GITHUB_URL}/blob/main/docs/texture_packs.md"
GUIDE_LINK = f'<a href="{DOCS_URL}" style="color: {theme.ORANGE};">the texture pack guide</a>'
COMPRESS_KEY = "texture_packs_compress"   # launcher.json: compress the PNGs of packs added (default on)
NO_COMPRESSOR = ("This build of the game has no texture compressor (it came with the game in a later "
                 "version): build the game again in Setup to get it.")


def _gb(n: int) -> str:
    return f"{n / 2**30:.1f} GB" if n >= 2**30 else f"{n / 2**20:.0f} MB"


class TexturePacksPage(FolderListPage):
    COLUMNS = ["Texture pack", "Author", "Textures", "Graphics memory"]
    NOUN = "texture pack"
    PLURAL = "texture packs"
    VERB = "be used"
    CONFLICT_UNIT = "texture(s)"
    ZIP_FILTER = "Texture packs (*.zip)"

    def __init__(self, cfg, window):
        super().__init__(cfg, window, "Texture packs",
                         "Higher-resolution textures, drawn by the graphics card in place of the "
                         "game's own. The game's files stay as they are.")
        self.task = None
        self.task_kind = ""   # "hd" (Make HD pack) or "compress" (Compress PNGs)
        self.plan = None
        lay = self.page_layout

        c, cl = card(spacing=14)
        self.switches = {}
        for key, title, text in (
                ("texture_replace", "Use texture packs",
                 "Draw the ticked packs' textures (Vulkan). Uses graphics memory, not the game's."),
                ("texture_dump", "Dump textures",
                 "Save each texture the game loads to textures/dump, named by its fingerprint: what "
                 "Make HD pack upscales, and the names pack files must have. Never share a dump.")):
            row = QHBoxLayout()
            row.setSpacing(20)
            col = QVBoxLayout()
            col.setSpacing(5)
            col.addWidget(label(title, "cardTitle"))
            col.addWidget(label(text, "muted"))
            row.addLayout(col, 1)
            sw = Switch()
            sw.setAccessibleName(title)
            sw.setFocusPolicy(Qt.StrongFocus)
            sw.toggled.connect(lambda on, key=key: self._set_option(key, on))
            row.addWidget(sw)
            self.switches[key] = sw
            cl.addLayout(row)
        self.vulkan_note = label("Texture packs need the Vulkan renderer (Settings > Display > Graphics "
                                 "API); Direct3D 12 only dumps.", "warn")
        cl.addWidget(self.vulkan_note)
        cl.addWidget(label("Both apply the next time the game starts.", "muted"))
        lay.addWidget(c)

        c, cl = card(spacing=14)
        cl.addWidget(label("Your texture packs", "cardTitle"))
        cl.addWidget(label("Ticked packs are used from top to bottom: when two packs replace the same "
                           "texture, the one lower in the list wins. You can also drop a pack's .zip "
                           "or folder on this page.", "muted"))
        self.build_list(cl, "Add pack…", "Open textures folder")
        self._compress_row(cl)
        lay.addWidget(c)

        self._hd_card(lay)

        c, cl = card(spacing=8)
        cl.addWidget(label("How texture packs work", "cardTitle"))
        cl.addWidget(label("A pack is a folder (or a .zip of one) of .dds or .png files, each named by "
                           "the fingerprint of the game texture it replaces, as in your dump, and "
                           "usually a pack.toml with its name. Packs hold changed game art: share only "
                           "your own work, never the game's textures or a dump.", "muted"))
        cl.addWidget(_link_label(f"Making, sharing and installing packs: {GUIDE_LINK}."))
        lay.addWidget(c)
        lay.addStretch()
        self.refresh()

    # ---- the list (FolderListPage hooks) --------------------------------------------------------
    def folder(self):
        return texture_packs.textures_dir(self.cfg)

    def scan(self):
        return texture_packs.listed(self.cfg)

    def conflicts(self, items):
        return texture_packs.conflicts(items)

    def remember(self, items):
        texture_packs.remember(self.cfg, items)

    def install(self, path, replace):
        if self.busy() and path.name == texture_packs.HD_PACK:
            raise texture_packs.InstallError("Make HD pack is writing that folder: wait for it to finish.")
        return texture_packs.install(path, self.folder(), replace=replace)

    def remove_folder(self, folder):
        if self.busy() and folder == texture_packs.HD_PACK:
            raise texture_packs.InstallError("Make HD pack is writing this pack: cancel it first.")
        texture_packs.remove(self.cfg, folder)

    def row_texts(self, p):
        return [p.author, f"{p.files} ({_size(p.size)})", _gb(p.gpu_bytes)]

    def empty_text(self):
        return (f"No texture packs yet. Use <b>Add pack…</b>, put a pack folder in {self.folder()}, "
                f"or make one from your dump below. How packs work: {GUIDE_LINK}.")

    def movable(self, p):
        return not p.loose

    def removable(self, p):
        return not p.loose

    def detail_lines(self, p):
        if p.loose:
            lines = [f"{p.files} texture file(s) directly in the textures folder, {_size(p.size)}: used "
                     "before every pack, so any pack replaces them. Untick to turn them off; to remove "
                     "them, move or delete them in the textures folder (Open textures folder)."]
        else:
            lines = [p.description] if p.description else []
            lines.append(f"{p.files} texture(s), {_size(p.size)} on disk, about {_gb(p.gpu_bytes)} of "
                         "graphics memory when every one is in use.")
        if p.png_files:
            line = (f"{p.png_files} of them are PNGs, {_gb(p.png_gpu_bytes)} of that graphics memory: Compress "
                    f"PNGs makes them DDS files (BC7) that take about a quarter of it.")
            lines.append(line if texture_compress.available(self.cfg) else f"{line} {NO_COMPRESSOR}")
        return lines

    def added_text(self, p):
        return f"Added {p.summary()} ({p.files} texture(s)).{' ' + p.note if p.note else ''}"

    def remove_question(self, p):
        made = " It was made on this PC: Make HD pack can make it again." if p.made_here else ""
        return f'Remove "{p.title}"? Its folder {p.folder} is deleted from the textures folder.{made}'

    def add_path(self, path):
        """Adds a pack; with Compress the PNGs of packs I add on, compresses its PNGs after."""
        if not super().add_path(path):
            return False
        p = self._current()
        if (p is not None and p.png_files and self.compress_added.isChecked() and not self.busy()
                and texture_compress.available(self.cfg)):
            self.compress_pack(p, ask=False)
        return True

    def _selected(self):
        super()._selected()
        if hasattr(self, "compress_button"):
            p = self._current()
            self.compress_button.setEnabled(bool(p is not None and p.png_files and not self.busy()
                                                 and texture_compress.available(self.cfg)))

    def refresh(self):
        super().refresh()
        for key, sw in self.switches.items():
            sw.blockSignals(True)
            sw.setChecked(bool(self.cfg.settings.get(key)))
            sw.blockSignals(False)
        vulkan = self.cfg.settings.get("gpu_backend", "vulkan") == "vulkan"
        self.vulkan_note.setVisible(not vulkan)
        self.switches["texture_replace"].setEnabled(vulkan)
        if self.items and not self.cfg.settings.get("texture_replace"):
            self._say(self.message.text() + " Use texture packs is off: turn it on above to use them.")
        self._refresh_hd()

    def _set_option(self, key, on):
        """Through the Settings page when there is one (it shows the change, saves and writes
        forza.toml), else straight into the config."""
        settings = getattr(self.window, "settings", None)
        if settings is not None and hasattr(settings, "apply_external"):
            settings.apply_external({key: on})
        else:
            self.cfg.settings[key] = schema.coerce(schema.BY_KEY[key], on)
            try:
                self.cfg.save()
            except OSError as e:
                self._say(f"Could not save the setting: {e}", error=True)
        self.refresh()

    # ---- Compress PNGs ------------------------------------------------------------------------
    def _compress_row(self, cl):
        row = QHBoxLayout()
        row.setSpacing(12)
        self.compress_added = QCheckBox("Compress the PNG textures of packs I add")
        self.compress_added.setToolTip("Makes them DDS files (BC7) that take about a quarter of the graphics "
                                       "memory, with mip levels. The pack's own files are replaced.")
        self.compress_added.setChecked(bool(self.cfg.d.get(COMPRESS_KEY, True)))
        self.compress_added.toggled.connect(self._set_compress_added)
        row.addWidget(self.compress_added)
        row.addStretch()
        self.compress_button = QPushButton("Compress PNGs")
        self.compress_button.setToolTip("Makes the selected pack's PNG textures DDS files (BC7, or BC1 where "
                                        "it keeps them as well) with mip levels: about a quarter of the "
                                        "graphics memory.")
        self.compress_button.clicked.connect(lambda: self.compress_pack(self._current()))
        row.addWidget(self.compress_button)
        self.compress_cancel = QPushButton("Cancel")
        self.compress_cancel.clicked.connect(self.cancel_task)
        self.compress_cancel.hide()
        row.addWidget(self.compress_cancel)
        cl.addLayout(row)
        self.compress_bar = QProgressBar()
        self.compress_bar.setRange(0, 1000)
        self.compress_bar.hide()
        cl.addWidget(self.compress_bar)
        self.compress_status = label("", "muted")
        self.compress_status.hide()
        cl.addWidget(self.compress_status)

    def _set_compress_added(self, on):
        self.cfg.d[COMPRESS_KEY] = bool(on)
        try:
            self.cfg.save()
        except OSError as e:
            self._say(f"Could not save the setting: {e}", error=True)

    def compress_pack(self, p, ask=True):
        """Compresses the PNGs of pack `p` in the background (texture_compress.compress_folder)."""
        if p is None or not p.png_files or self.busy():
            return
        if not texture_compress.available(self.cfg):
            self.compress_status.setText(NO_COMPRESSOR)
            self.compress_status.show()
            return
        if ask and not self._confirm("Compress PNGs", (
                f'Compress the {p.png_files} PNG texture(s) of "{p.title}"? They become DDS files (BC7, or BC1 '
                f"where it keeps them as well) that take about a quarter of their {_gb(p.png_gpu_bytes)} of "
                "graphics memory, with mip levels. The PNGs are replaced; it applies from the next start.")):
            return
        cfg, folder, title = self.cfg, self.folder(), p.title
        target = folder if p.loose else folder / p.folder
        work = folder / f".compressing-{'loose' if p.loose else p.folder}"

        def job(task):
            try:
                result = texture_compress.compress_folder(
                    cfg, target, work, lambda n, total: task.progress.emit(
                        n / total if total else 1.0, f'Compressing "{title}": {n} of {total}'),
                    task.cancelled, top_only=p.loose)
            except (RuntimeError, OSError) as e:
                raise installer.InstallError(f"Compressing stopped: {e}") from e
            words = (f'Compressed {result.compressed} texture(s) of "{title}": {_gb(result.png_bytes)} of graphics '
                     f"memory as PNGs, {_gb(result.dds_bytes)} now.")
            if result.failed:
                words += f" {len(result.failed)} could not be compressed (first: {result.failed[0]})."
            return words

        self._start_task(job, "compress PNGs", "compress")
        self.compress_status.setText(f'Compressing "{title}"…')

    def _start_task(self, job, name, kind):
        from .workers import Task
        self.task, self.task_kind = Task(job, name), kind
        self.task.progress.connect(self._task_progress)
        self.task.done.connect(self._task_done)
        bar = self.bar if kind == "hd" else self.compress_bar
        bar.setValue(0)
        bar.show()
        status = self.hd_status if kind == "hd" else self.compress_status
        status.setStyleSheet("")
        status.show()
        self.compress_cancel.setVisible(kind == "compress")
        self.task.start()
        self._refresh_hd()
        self._selected()

    def _task_progress(self, fraction, text):
        bar, status = (self.bar, self.hd_status) if self.task_kind == "hd" else (self.compress_bar,
                                                                                 self.compress_status)
        bar.setValue(int(max(0.0, min(1.0, fraction)) * 1000))
        status.setText(text)

    def _task_done(self, ok, message):
        kind = self.task_kind
        self.task.wait()
        self.task, self.task_kind = None, ""
        self.compress_cancel.hide()
        if kind == "hd":
            self._hd_done(ok, message)
            return
        self.compress_bar.hide()
        if not ok and message == "Cancelled.":
            message = "Stopped. The textures compressed so far stay compressed."
        self.compress_status.setText(message)
        self.compress_status.setStyleSheet("" if ok or "Stopped" in message else f"color: {theme.ERR};")
        self.refresh()

    def cancel_task(self):
        if self.busy():
            self.task.cancel()
            if self.task_kind == "compress":
                self.compress_status.setText("Stopping… the textures compressed so far stay compressed.")

    # ---- Make HD pack -------------------------------------------------------------------------
    def _hd_card(self, lay):
        c, cl = card(spacing=12)
        cl.addWidget(label("Make an HD pack", "cardTitle"))
        size = pins.SIZES_MB.get(hd_pack.platform_key(), 45)
        cl.addWidget(label(
            "Upscales the textures you dumped, on this PC, with Real-ESRGAN (an AI upscaler, free "
            "software, any graphics card): rocks, roads, terrain, bark and signs get sharper. Masks, "
            "normal and data maps and small textures are left as they are. The textures are compressed "
            "(BC7 DDS files with mip levels, a quarter of the graphics memory of PNGs). The result is the "
            f"pack \"HD pack (made on this PC)\". The first time, the upscaler ({size} MB) is downloaded "
            "from its official release; nothing of the game is downloaded or shared.", "muted"))
        link = f'style="color: {theme.ORANGE};"'
        cl.addWidget(_link_label(
            f'Licences: the program <a href="{hd_pack.PROGRAM_LICENCE_URL}" {link}>Real-ESRGAN-ncnn-vulkan</a> '
            f'is MIT, the model <a href="{hd_pack.MODEL_LICENCE_URL}" {link}>realesrgan-x4plus</a> (Real-ESRGAN) '
            f'BSD-3-Clause. The launcher downloads them from the <a href="{hd_pack.RELEASE_URL}" {link}>official '
            "release</a>, checks their pinned checksums and never ships them. The compressor, built with the "
            f'game, uses <a href="{texture_compress.LICENCE_URL}" {link}>bc7enc_rdo</a> (MIT).'))
        row = QHBoxLayout()
        row.setSpacing(12)
        self.scale = QComboBox()
        self.scale.addItem("2x (recommended)", 2)
        self.scale.addItem("4x", 4)
        self.scale.setAccessibleName("Upscale")
        self.scale.setMinimumWidth(330)
        self.scale.currentIndexChanged.connect(lambda _i: self._refresh_hd())
        row.addWidget(self.scale)
        self.make_button = QPushButton("Make HD pack")
        self.make_button.clicked.connect(self.make_hd_pack)
        row.addWidget(self.make_button)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.cancel_hd_pack)
        self.cancel_button.hide()
        row.addWidget(self.cancel_button)
        row.addStretch()
        cl.addLayout(row)
        self.scale_note = label("", "muted")
        self.scale_note.hide()
        cl.addWidget(self.scale_note)
        self.estimate = label("", "muted")
        cl.addWidget(self.estimate)
        self.hd_warning = label("", "warn")
        self.hd_warning.hide()
        cl.addWidget(self.hd_warning)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.hide()
        cl.addWidget(self.bar)
        self.hd_status = label("", "muted")
        cl.addWidget(self.hd_status)
        lay.addWidget(c)

    def busy(self) -> bool:
        return self.task is not None and self.task.isRunning()

    def hd_busy(self) -> bool:
        return self.busy() and self.task_kind == "hd"

    def _min_vram_4x(self) -> int:
        return hd_pack.min_vram_gb_4x(texture_compress.available(self.cfg))

    def _scale(self) -> int:
        return int(self.scale.currentData() or 2)

    def four_x_refused(self) -> str:
        """Why 4x is not offered ("" when it is): it needs a card with at least
        hd_pack.min_vram_gb_4x() GB of its own memory (the launcher's GPU detection, #206): 6 for a
        compressed pack, 10 for PNGs (a build of the game without the compressor)."""
        s = self.cfg.pc_specs()
        need = self._min_vram_4x()
        if s.integrated is True:
            return (f"4x needs a graphics card with at least {need} GB of its own memory; "
                    "integrated graphics share the PC's.")
        if s.vram_gb <= 0:
            return (f"4x needs a graphics card with at least {need} GB of its own memory, "
                    "and the launcher could not tell how much yours has.")
        if s.vram_gb < need:
            return (f"4x needs a graphics card with at least {need} GB of its own memory; "
                    f"yours has {s.vram_gb:.0f} GB.")
        return ""

    def _scale_items(self):
        """Each choice with its pack's graphics memory; 4x greyed out (with the reason) on a card
        with too little memory."""
        refused = self.four_x_refused()
        plans = {}
        for index in range(self.scale.count()):
            scale = int(self.scale.itemData(index))
            plans[scale] = hd_pack.plan(self.cfg, scale)
            gpu = plans[scale].gpu_bytes()
            name = "2x (recommended)" if scale == 2 else "4x"
            self.scale.setItemText(index, f"{name}: about {_gb(gpu)} of graphics memory" if gpu else name)
            if scale == 4:
                item = self.scale.model().item(index)
                if item is not None:
                    item.setEnabled(not refused)
                    item.setToolTip(refused)
        if refused and self._scale() == 4:
            self.scale.setCurrentIndex(self.scale.findData(2))
        self.scale_note.setText(refused)
        self.scale_note.setVisible(bool(refused))
        return plans

    def _refresh_hd(self):
        if not hasattr(self, "scale"):
            return
        running = self.hd_busy()
        self.make_button.setVisible(not running)
        self.cancel_button.setVisible(running)
        self.scale.setEnabled(not running)
        if running:
            return
        self.scale.blockSignals(True)
        plans = self._scale_items()
        self.scale.blockSignals(False)
        self.plan = p = plans[self._scale()]
        if not p.dumped:
            self.estimate.setText("No texture dump yet: turn on Dump textures above, play and drive where "
                                  "you want sharper textures (a race dumps about 1,300), then come back.")
            self.make_button.setEnabled(False)
            self.hd_warning.hide()
            return
        skipped = sum(p.skipped.values())
        why = ", ".join(f"{n:,} {reason}" for reason, n in p.skipped.most_common())
        if p.todo:
            made = f" ({p.done:,} already made)" if p.done else ""
            lines = [f"{p.dumped:,} textures dumped: {p.count:,} to upscale{made}"]
        else:
            lines = [f"{p.dumped:,} textures dumped: all {p.count:,} to upscale are in the HD pack"]
        lines[0] += f"; {skipped:,} left as they are ({why})." if skipped else "."
        if p.replaces:
            lines.append(f"Making it at {p.scale}x replaces your {p.replaces}x HD pack.")
        if p.count:
            minutes = max(1, round(p.seconds() / 60))
            lines.append(f"The pack: about {_gb(p.disk_bytes())} on disk and {_gb(p.gpu_bytes())} of graphics "
                         "memory with Use texture packs on."
                         + (f" About {minutes} min on a mid-range graphics card; the game can run meanwhile."
                            if p.todo else " Dump more textures (drive somewhere else) to add to it."))
            if p.compressed and p.png_done:
                lines.append(f"{p.png_done:,} made before as PNGs are compressed first.")
            if not p.compressed:
                lines.append("Written as PNGs, which take 4 times the graphics memory of compressed textures. "
                             + NO_COMPRESSOR)
        self.estimate.setText(" ".join(lines))
        self.make_button.setEnabled(bool(p.todo or (p.compressed and p.png_done)) and not self.busy())
        self.make_button.setText("Continue HD pack" if p.done and p.todo else
                                 "Compress HD pack" if p.compressed and p.png_done and not p.todo else
                                 "Make HD pack")
        warning = self._gpu_warning(p)
        self.hd_warning.setText(warning)
        self.hd_warning.setVisible(bool(warning))

    def _gpu_warning(self, p) -> str:
        s = self.cfg.pc_specs()
        tier = specs.gpu_tier(s)
        need = p.gpu_bytes()
        if tier == specs.LOW:
            return ("Your graphics card is integrated or has little memory: making the pack can take hours, "
                    f"and its {_gb(need)} of graphics memory can make the game stutter. Use 2x, or leave "
                    "texture packs off.")
        if s.vram_gb and need > s.vram_gb * 2**30 * 0.4:
            return (f"The pack needs about {_gb(need)} of graphics memory, a lot for your card's "
                    f"{s.vram_gb:.0f} GB: textures may load slowly or the game run out of memory. "
                    + ("Use 2x." if p.scale == 4 else "Leave Use texture packs off if the game stutters."))
        return ""

    def make_hd_pack(self):
        if self.busy() or self.plan is None:
            return
        scale = self._scale()
        if scale == 4 and self.four_x_refused():
            self.hd_status.setText(self.four_x_refused())
            return
        before = hd_pack.existing_scale(self.cfg)
        if before and before != scale and hd_pack.pack_dir(self.cfg).is_dir():
            if not self._confirm("Make HD pack", f"Your HD pack was made at {before}x. Making it at {scale}x "
                                                 "replaces it. Continue?"):
                return
            try:
                texture_packs.remove(self.cfg, texture_packs.HD_PACK)
            except texture_packs.InstallError as e:
                self.hd_status.setText(str(e))
                return
        if not hd_pack.installed(self.cfg):
            size = pins.SIZES_MB.get(hd_pack.platform_key(), 45)
            if not self._confirm("Make HD pack", f"Download the upscaler (Real-ESRGAN, {size} MB) from its "
                                                 "official release on GitHub? It is checked against its "
                                                 "known checksum and kept in the launcher's tools folder."):
                return
        cfg = self.cfg

        def job(task):
            try:
                if not hd_pack.installed(cfg):
                    task.progress.emit(0.0, "Downloading the upscaler…")
                    hd_pack.install(cfg, lambda done, total: task.progress.emit(
                        done / total if total else 0.0, f"Downloading the upscaler… {done >> 20} MB"),
                        task.cancelled)
                result = hd_pack.make(cfg, scale, progress=lambda f, text: task.progress.emit(f, text),
                                      cancelled=task.cancelled)
            except (RuntimeError, OSError) as e:  # words for the player; Task logs the details
                raise installer.InstallError(f"Make HD pack stopped: {e}") from e
            words = f"Made {result.made} texture(s) in {max(1, round(result.seconds / 60))} min"
            if result.compressed:
                formats = ", ".join(f"{n} {f}" for f, n in sorted(result.formats.items(), key=lambda x: -x[1]))
                words += f" ({formats})" if formats else ""
                if result.converted:
                    words += f"; {result.converted} made before compressed"
            words += "."
            if result.left_out:
                words += (f" {sum(result.left_out.values())} looked like normal or data maps and were "
                          "left as they are.")
            if result.failed:
                words += f" {len(result.failed)} could not be made (first: {result.failed[0]})."
            return words

        self._start_task(job, "make HD pack", "hd")
        self.hd_status.setText("Starting…")

    def cancel_hd_pack(self):
        if self.hd_busy():
            self.task.cancel()
            self.hd_status.setText("Stopping… the textures made so far are kept; Make HD pack continues "
                                   "from there.")

    def _hd_done(self, ok, message):
        self.bar.hide()
        if not ok and message == "Cancelled.":
            message = "Stopped. The textures made so far are kept: Make HD pack continues from there."
        self.hd_status.setText(message)
        self.hd_status.setStyleSheet("" if ok or "Stopped" in message else f"color: {theme.ERR};")
        self.refresh()
        if ok and not self.cfg.settings.get("texture_replace"):
            self.hd_status.setText(message + " Turn on Use texture packs above to see it in the game.")

    def shutdown(self, wait_ms=15000) -> bool:
        """Stops Make HD pack or Compress PNGs (when the window closes); False when it did not stop
        in time."""
        if not self.busy():
            return True
        self.task.cancel()
        return self.task.wait(wait_ms)
