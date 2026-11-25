# -*- coding: utf-8 -*-
"""Small draggable animated desktop companion drawn with Tk Canvas."""
from __future__ import annotations
import tkinter as tk


class AvatarWindow:
    def __init__(self, root: tk.Tk, name: str = "Ишка") -> None:
        self.root = root
        self.name = name
        self.window = tk.Toplevel(root)
        self.window.title(name)
        self.window.geometry("210x245+80+90")
        self.window.resizable(False, False)
        self.window.attributes("-topmost", True)
        self.window.configure(bg="#101827")
        self.window.protocol("WM_DELETE_WINDOW", self.hide)
        self.canvas = tk.Canvas(self.window, width=210, height=245, bg="#101827", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.phase = 0
        self.blink = False
        self.talking = False
        self.visible = True
        self._drag_origin: tuple[int, int] | None = None
        self.canvas.bind("<ButtonPress-1>", self._drag_start)
        self.canvas.bind("<B1-Motion>", self._drag_move)
        self.canvas.bind("<Double-Button-1>", lambda _e: self.toggle_topmost())
        self._animate()

    def _drag_start(self, event: tk.Event) -> None:
        self._drag_origin = (event.x_root - self.window.winfo_x(), event.y_root - self.window.winfo_y())

    def _drag_move(self, event: tk.Event) -> None:
        if self._drag_origin:
            x = event.x_root - self._drag_origin[0]
            y = event.y_root - self._drag_origin[1]
            self.window.geometry(f"+{x}+{y}")

    def toggle_topmost(self) -> None:
        self.window.attributes("-topmost", not bool(self.window.attributes("-topmost")))

    def set_talking(self, value: bool) -> None:
        self.talking = bool(value)

    def show(self) -> None:
        self.visible = True
        self.window.deiconify()
        self.window.lift()

    def hide(self) -> None:
        self.visible = False
        self.window.withdraw()

    def _animate(self) -> None:
        if not self.window.winfo_exists():
            return
        self.phase += 1
        bob = 3 if (self.phase // 8) % 2 == 0 else -3
        self.canvas.delete("all")
        self.canvas.create_text(105, 18, text=f"{self.name}  ·  твой ИИ", fill="#a9bbd4", font=("Segoe UI", 9, "bold"))
        # Glow and body.
        self.canvas.create_oval(41, 36 + bob, 169, 164 + bob, fill="#1f4d82", outline="#377dd0", width=2)
        self.canvas.create_oval(52, 47 + bob, 158, 153 + bob, fill="#8dc8ff", outline="#c9e7ff", width=2)
        self.canvas.create_oval(66, 60 + bob, 144, 138 + bob, fill="#d9efff", outline="")
        if self.blink:
            self.canvas.create_line(81, 93 + bob, 95, 93 + bob, fill="#17375a", width=4, capstyle="round")
            self.canvas.create_line(115, 93 + bob, 129, 93 + bob, fill="#17375a", width=4, capstyle="round")
        else:
            self.canvas.create_oval(82, 83 + bob, 94, 101 + bob, fill="#17375a", outline="")
            self.canvas.create_oval(116, 83 + bob, 128, 101 + bob, fill="#17375a", outline="")
            self.canvas.create_oval(86, 86 + bob, 90, 91 + bob, fill="#ffffff", outline="")
            self.canvas.create_oval(120, 86 + bob, 124, 91 + bob, fill="#ffffff", outline="")
        if self.talking and self.phase % 2:
            self.canvas.create_oval(96, 112 + bob, 114, 128 + bob, fill="#9a405d", outline="#9a405d")
        else:
            self.canvas.create_arc(91, 107 + bob, 119, 132 + bob, start=205, extent=130, style="arc", outline="#17436d", width=3)
        self.canvas.create_text(105, 187, text="Дважды щёлкни: закрепить / открепить", fill="#90a4bf", font=("Segoe UI", 7))
        self.canvas.create_text(105, 211, text="Перетаскивай меня мышью", fill="#90a4bf", font=("Segoe UI", 8))
        self.blink = self.phase % 21 == 0 or (self.phase % 21 == 1)
        self.window.after(150, self._animate)
