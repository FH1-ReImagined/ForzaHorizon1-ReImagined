"""Review and edit a local crash report before opening a GitHub issue draft."""
from PySide6.QtCore import Qt, QUrl
from PySide6.QtWidgets import (QApplication, QDialog, QFileDialog, QHBoxLayout, QLabel,
                               QLineEdit, QPlainTextEdit, QPushButton, QVBoxLayout)

from .. import browser, crash_report, paths
from .widgets import label


class CrashReportDialog(QDialog):
    def __init__(self, report, parent=None):
        super().__init__(parent)
        self.report = report
        self.setWindowTitle("Review crash report")
        self.resize(780, 700)
        self.setMinimumSize(620, 500)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(14)
        heading = QLabel("The game stopped unexpectedly")
        heading.setObjectName("cardTitle")
        lay.addWidget(heading)
        lay.addWidget(label("Add what you were doing and review the details below. Known paths and "
                            "profile IDs have been removed, but logs may still contain personal details. "
                            "GitHub opens a draft; you decide when to submit it.", "muted"))
        caption = QLabel("Issue title")
        self.title = QLineEdit(report.title)
        self.title.setMaxLength(200)
        caption.setBuddy(self.title)
        lay.addWidget(caption)
        lay.addWidget(self.title)
        self.body = QPlainTextEdit(report.body)
        self.body.setAccessibleName("Crash report details")
        lay.addWidget(self.body, 1)
        self.notice = label("", "muted")
        self.notice.setTextFormat(Qt.PlainText)
        self.notice.hide()
        lay.addWidget(self.notice)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.copy = QPushButton("Copy report")
        self.copy.clicked.connect(self.copy_report)
        row.addWidget(self.copy)
        self.save = QPushButton("Save report…")
        self.save.clicked.connect(self.save_report)
        row.addWidget(self.save)
        row.addStretch(1)
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.reject)
        row.addWidget(self.close_button)
        self.github = QPushButton("Open GitHub issue")
        self.github.clicked.connect(self.open_issue)
        self.github.setAutoDefault(False)
        row.addWidget(self.github)
        lay.addLayout(row)
        self.title.textChanged.connect(self._validate)
        self._validate()

    def _validate(self):
        self.github.setEnabled(bool(self.title.text().strip()))

    def message(self, text):
        self.notice.setText(text)
        self.notice.show()

    def copy_report(self):
        QApplication.clipboard().setText(self.body.toPlainText())
        self.message("Report copied. You can paste it into an issue or share it yourself.")

    def save_report(self):
        filename, _ = QFileDialog.getSaveFileName(self, "Save crash report", "forza-crash-report.md",
                                                 "Markdown (*.md);;Text (*.txt)")
        if not filename:
            return
        try:
            from pathlib import Path
            Path(filename).write_text(self.body.toPlainText(), encoding="utf-8")
        except OSError as error:
            paths.log_error("save crash report", error)
            self.message(f"Could not save the report: {error}")
        else:
            self.message(f"Report saved to {filename}")

    def open_issue(self):
        title, body = self.title.text().strip(), self.body.toPlainText()
        if not title:
            return
        url = crash_report.issue_url(title, body)
        copied = url is None
        if copied:
            # Large logs cannot fit in a portable browser URL. Preserve every reviewed byte
            # on the clipboard and explicitly ask the player to paste it into the draft.
            QApplication.clipboard().setText(body)
            url = crash_report.issue_url(title, "Paste the crash report copied by the launcher here.")
        if not browser.open_url(QUrl(url)):
            self.message("Could not open your browser. Copy or save the report and open the project's "
                         "Issues page on GitHub.")
        elif copied:
            self.message("The report is too long for a browser link. It has been copied: paste it "
                         "into the GitHub issue description, then submit when ready.")
        else:
            self.message("GitHub opened with your report. Review it there and submit when ready.")
