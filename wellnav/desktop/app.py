"""Tkinter desktop manager for remote Well Navigation SQLite files."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from wellnav.desktop.remote import DEFAULT_HOST, RemoteStore, format_size
from wellnav.desktop.store import DATA_DIR

BG = "#12160f"
PANEL = "#1b2117"
PANEL2 = "#242b1e"
INK = "#e8ecd9"
MUTED = "#9aa386"
LINE = "#3a422f"
ACCENT = "#d4a017"
FIELD = "#10140d"

WRITE_START = {"insert", "update", "delete", "drop", "alter", "create", "replace", "vacuum", "reindex"}


class DatabaseApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Well Navigation — Remote Databases")
        self.geometry("1280x780")
        self.minsize(960, 600)
        self.configure(bg=BG)
        self.host_var = tk.StringVar(value=DEFAULT_HOST)
        self.store = RemoteStore(self.host_var.get())
        self.current_db: str | None = None
        self.current_table: str | None = None
        self.page_offset = 0
        self.page_limit = tk.StringVar(value="100")
        self.hide_empty = tk.BooleanVar(value=True)
        self.search_var = tk.StringVar()
        self.status_var = tk.StringVar(value=f"Connecting to {DEFAULT_HOST}…")
        self.raw_rows: list[dict] = []
        self.sort_column: str | None = None
        self.sort_desc = False
        self._ui_queue: queue.Queue = queue.Queue()

        self._style()
        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(50, self._poll_ui)
        self.after(80, self.reconnect)

    def _style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=INK, bordercolor=LINE, font=("Segoe UI", 10))
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=INK)
        style.configure("Muted.TLabel", background=BG, foreground=MUTED)
        style.configure("Panel.TLabel", background=PANEL, foreground=INK)
        style.configure("TCheckbutton", background=PANEL, foreground=INK, focuscolor=PANEL)
        style.configure("TNotebook", background=BG, bordercolor=LINE)
        style.configure("TNotebook.Tab", background=PANEL2, foreground=INK, padding=(12, 6))
        style.map("TNotebook.Tab", background=[("selected", ACCENT)], foreground=[("selected", "#1a1404")])
        style.configure(
            "Treeview",
            background=PANEL,
            fieldbackground=PANEL,
            foreground=INK,
            bordercolor=LINE,
            rowheight=24,
        )
        style.configure("Treeview.Heading", background=PANEL2, foreground=INK, relief="flat")
        style.map("Treeview", background=[("selected", ACCENT)], foreground=[("selected", "#1a1404")])
        style.configure("TButton", background=PANEL2, foreground=INK, padding=(10, 5), bordercolor=LINE)
        style.map("TButton", background=[("active", LINE)])
        style.configure("Accent.TButton", background=ACCENT, foreground="#1a1404")
        style.map("Accent.TButton", background=[("active", "#e0b12a")])
        style.configure("TCombobox", fieldbackground=FIELD, foreground=INK, background=PANEL2)
        style.configure("TEntry", fieldbackground=FIELD, foreground=INK)
        style.configure("TPanedwindow", background=BG)
        style.configure("TSeparator", background=LINE)

    def _build(self) -> None:
        menubar = tk.Menu(self, tearoff=0, bg=PANEL, fg=INK, activebackground=ACCENT, activeforeground="#1a1404")
        file_menu = tk.Menu(menubar, tearoff=0, bg=PANEL, fg=INK)
        file_menu.add_command(label="Reconnect", command=self.reconnect)
        file_menu.add_command(label="Refresh", command=self.refresh_databases, accelerator="F5")
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._on_close)
        menubar.add_cascade(label="File", menu=file_menu)
        self.config(menu=menubar)
        self.bind("<F5>", lambda _e: self.refresh_databases())

        top = ttk.Frame(self)
        top.pack(fill="x", padx=12, pady=(10, 6))
        ttk.Label(top, text="●", foreground=ACCENT).pack(side="left")
        ttk.Label(top, text="  Well Navigation").pack(side="left")
        ttk.Label(top, text="Remote host", style="Muted.TLabel").pack(side="left", padx=(12, 6))
        host_entry = ttk.Entry(top, textvariable=self.host_var, width=16)
        host_entry.pack(side="left")
        ttk.Button(top, text="Connect", style="Accent.TButton", command=self.reconnect).pack(side="left", padx=(8, 0))
        ttk.Button(top, text="Refresh", command=self.refresh_databases).pack(side="right")

        panes = ttk.Panedwindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True, padx=12, pady=(0, 8))

        left = ttk.Frame(panes, style="Panel.TFrame")
        right = ttk.Frame(panes)
        panes.add(left, weight=1)
        panes.add(right, weight=3)

        ttk.Label(left, text="Databases", style="Panel.TLabel").pack(anchor="w", padx=10, pady=(10, 4))
        self.db_list = ttk.Treeview(left, columns=("meta",), show="tree headings", height=6, selectmode="browse")
        self.db_list.heading("#0", text="File")
        self.db_list.heading("meta", text="Size")
        self.db_list.column("#0", width=180)
        self.db_list.column("meta", width=140)
        self.db_list.pack(fill="x", padx=10)
        self.db_list.bind("<<TreeviewSelect>>", self._on_db_select)

        table_head = ttk.Frame(left, style="Panel.TFrame")
        table_head.pack(fill="x", padx=10, pady=(12, 4))
        ttk.Label(table_head, text="Tables", style="Panel.TLabel").pack(side="left")
        ttk.Checkbutton(
            table_head,
            text="Hide empty",
            variable=self.hide_empty,
            command=self.refresh_tables,
        ).pack(side="right")

        table_frame = ttk.Frame(left, style="Panel.TFrame")
        table_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.table_list = ttk.Treeview(table_frame, columns=("count",), show="headings", selectmode="browse")
        self.table_list.heading("count", text="Rows")
        self.table_list.heading("#0", text="Table")
        self.table_list.configure(show="tree headings")
        self.table_list.column("#0", width=170)
        self.table_list.column("count", width=90, anchor="e")
        scroll_t = ttk.Scrollbar(table_frame, orient="vertical", command=self.table_list.yview)
        self.table_list.configure(yscrollcommand=scroll_t.set)
        self.table_list.pack(side="left", fill="both", expand=True)
        scroll_t.pack(side="right", fill="y")
        self.table_list.bind("<<TreeviewSelect>>", self._on_table_select)

        tabs = ttk.Notebook(right)
        tabs.pack(fill="both", expand=True)
        self.tabs = tabs

        browse = ttk.Frame(tabs, padding=8)
        sql_tab = ttk.Frame(tabs, padding=8)
        maint = ttk.Frame(tabs, padding=8)
        tabs.add(browse, text="Browse")
        tabs.add(sql_tab, text="SQL")
        tabs.add(maint, text="Maintenance")

        bar = ttk.Frame(browse)
        bar.pack(fill="x", pady=(4, 6))
        ttk.Label(bar, text="Filter").pack(side="left")
        search = ttk.Entry(bar, textvariable=self.search_var)
        search.pack(side="left", fill="x", expand=True, padx=8)
        search.bind("<Return>", lambda _e: self.load_page(reset=True))
        ttk.Button(bar, text="Apply", command=lambda: self.load_page(reset=True)).pack(side="left")
        ttk.Button(bar, text="Export CSV…", command=self.export_csv).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="Delete selected", command=self.delete_selected).pack(side="left", padx=(8, 0))

        tree_wrap = ttk.Frame(browse)
        tree_wrap.pack(fill="both", expand=True)
        self.grid = ttk.Treeview(tree_wrap, show="headings", selectmode="extended")
        yscroll = ttk.Scrollbar(tree_wrap, orient="vertical", command=self.grid.yview)
        xscroll = ttk.Scrollbar(tree_wrap, orient="horizontal", command=self.grid.xview)
        self.grid.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.grid.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        tree_wrap.rowconfigure(0, weight=1)
        tree_wrap.columnconfigure(0, weight=1)

        pager = ttk.Frame(browse)
        pager.pack(fill="x", pady=(6, 0))
        ttk.Button(pager, text="◀ Prev", command=self.prev_page).pack(side="left")
        ttk.Button(pager, text="Next ▶", command=self.next_page).pack(side="left", padx=(6, 12))
        ttk.Label(pager, text="Page size").pack(side="left")
        sizes = ttk.Combobox(pager, values=("50", "100", "250", "500"), width=6, state="readonly", textvariable=self.page_limit)
        sizes.pack(side="left", padx=6)
        sizes.bind("<<ComboboxSelected>>", lambda _e: self.load_page(reset=True))
        self.page_label = ttk.Label(pager, text="", style="Muted.TLabel")
        self.page_label.pack(side="left", padx=10)

        sql_bar = ttk.Frame(sql_tab)
        sql_bar.pack(fill="x", pady=(4, 6))
        ttk.Button(sql_bar, text="Run", style="Accent.TButton", command=self.run_sql).pack(side="left")
        ttk.Label(sql_bar, text="Ctrl+Enter", style="Muted.TLabel").pack(side="left", padx=8)
        self.sql_text = tk.Text(
            sql_tab,
            height=10,
            bg=FIELD,
            fg=INK,
            insertbackground=INK,
            relief="flat",
            font=("Consolas", 11),
            wrap="none",
        )
        self.sql_text.pack(fill="x")
        self.sql_text.bind("<Control-Return>", lambda _e: self.run_sql() or "break")
        sql_holder = ttk.Frame(sql_tab)
        sql_holder.pack(fill="both", expand=True, pady=(8, 0))
        self.sql_grid = ttk.Treeview(sql_holder, show="headings")
        sql_y = ttk.Scrollbar(sql_holder, orient="vertical", command=self.sql_grid.yview)
        sql_x = ttk.Scrollbar(sql_holder, orient="horizontal", command=self.sql_grid.xview)
        self.sql_grid.configure(yscrollcommand=sql_y.set, xscrollcommand=sql_x.set)
        self.sql_grid.grid(row=0, column=0, sticky="nsew")
        sql_y.grid(row=0, column=1, sticky="ns")
        sql_x.grid(row=1, column=0, sticky="ew")
        sql_holder.rowconfigure(0, weight=1)
        sql_holder.columnconfigure(0, weight=1)

        actions = ttk.Frame(maint)
        actions.pack(fill="x", pady=8)
        ttk.Button(actions, text="Integrity check", command=self.integrity).pack(side="left")
        ttk.Button(actions, text="WAL checkpoint", command=self.checkpoint).pack(side="left", padx=6)
        ttk.Button(actions, text="Vacuum", command=self.vacuum).pack(side="left")
        ttk.Button(actions, text="Compact backup…", command=self.backup).pack(side="left", padx=6)
        ttk.Button(actions, text="Replace from file…", command=self.restore).pack(side="left")

        self.maint_log = tk.Text(maint, bg=FIELD, fg=INK, insertbackground=INK, relief="flat", font=("Consolas", 10), wrap="word")
        self.maint_log.pack(fill="both", expand=True, pady=(8, 0))
        self.maint_log.insert("end", "Maintenance runs against the selected database on the remote host.\n")
        self.maint_log.configure(state="disabled")

        status = ttk.Frame(self)
        status.pack(fill="x", padx=12, pady=(0, 8))
        ttk.Label(status, textvariable=self.status_var, style="Muted.TLabel").pack(side="left")

    def _on_close(self) -> None:
        self.store.close()
        self.destroy()

    def set_status(self, text: str) -> None:
        self.status_var.set(text)

    def _fail(self, exc: Exception) -> None:
        self.set_status(str(exc))
        messagebox.showerror("Database error", str(exc), parent=self)

    def _poll_ui(self) -> None:
        try:
            while True:
                callback = self._ui_queue.get_nowait()
                callback()
        except queue.Empty:
            pass
        self.after(50, self._poll_ui)

    def _bg(self, fn, on_ok) -> None:
        def worker() -> None:
            try:
                result = fn()
            except Exception as exc:
                self._ui_queue.put(lambda err=exc: self._fail(err))
            else:
                self._ui_queue.put(lambda value=result: on_ok(value))

        threading.Thread(target=worker, daemon=True).start()

    def reconnect(self) -> None:
        host = self.host_var.get().strip() or DEFAULT_HOST
        self.store.close()
        self.store = RemoteStore(host)
        self.set_status(f"Connecting to {host}…")

        def work():
            return self.store.connect()

        def done(info):
            self.set_status(f"Connected to {host} · {info.get('data_root', '/app/data')}")
            self.refresh_databases()

        self._bg(work, done)

    def refresh_databases(self, select: str | None = None) -> None:
        if not self.store.connected:
            self.set_status("Not connected. Click Connect.")
            return

        def work():
            return self.store.list_databases()

        def done(rows):
            for item in self.db_list.get_children():
                self.db_list.delete(item)
            chosen = None
            for row in rows:
                iid = str(row["path"])
                self.db_list.insert(
                    "",
                    "end",
                    iid=iid,
                    text=row["name"],
                    values=(f"{row['total_label']} · {row['tables']} tables",),
                )
                if select and iid == str(select):
                    chosen = iid
                if chosen is None and row["name"] == "wellnav.db":
                    chosen = iid
            if chosen:
                self.db_list.selection_set(chosen)
                self.db_list.focus(chosen)
                self._on_db_select()
            elif not rows:
                self.set_status(f"No .db files on {self.host_var.get()} /app/data")

        self._bg(work, done)

    def _on_db_select(self, _event=None) -> None:
        sel = self.db_list.selection()
        if not sel:
            return
        self.current_db = sel[0]

        def work():
            return self.store.file_info(self.current_db)

        def done(info):
            extra = f" + WAL {format_size(info['wal_size'])}" if info.get("wal_size") else ""
            self.set_status(f"{self.host_var.get()}:{info['path']} · {info['total_label']}{extra}")
            self.refresh_tables()

        self._bg(work, done)

    def refresh_tables(self) -> None:
        if not self.current_db:
            return
        path = self.current_db
        hide = self.hide_empty.get()

        def work():
            return self.store.list_tables(path, hide_empty=hide)

        def done(rows):
            for item in self.table_list.get_children():
                self.table_list.delete(item)
            for row in rows:
                count = "" if row["count"] is None else f"{row['count']:,}"
                self.table_list.insert("", "end", iid=row["name"], text=row["name"], values=(count,))
            self.set_status(f"{self.host_var.get()}:{Path(str(path)).name} · {len(rows)} tables shown")

        self._bg(work, done)

    def _on_table_select(self, _event=None) -> None:
        sel = self.table_list.selection()
        if not sel:
            return
        self.current_table = sel[0]
        self.sort_column = None
        self.sort_desc = False
        self.tabs.select(0)
        self.load_page(reset=True)

    def load_page(self, reset: bool = False) -> None:
        if not self.current_db or not self.current_table:
            return
        if reset:
            self.page_offset = 0
        path = self.current_db
        table = self.current_table
        offset = self.page_offset
        try:
            limit = int(self.page_limit.get())
        except (TypeError, ValueError):
            limit = 100
        search = self.search_var.get()
        order = self.sort_column
        desc = self.sort_desc

        def work():
            return self.store.browse(
                path,
                table,
                offset=offset,
                limit=limit,
                order_by=order,
                descending=desc,
                search=search,
            )

        def done(page):
            self.raw_rows = page["raw"]
            columns = [c["name"] for c in page["columns"]]
            self.grid.delete(*self.grid.get_children())
            self.grid["columns"] = columns
            for name in columns:
                self.grid.heading(name, text=name, command=lambda col=name: self._sort_by(col))
                self.grid.column(name, width=max(90, min(220, 10 * len(name) + 40)), stretch=False)
            for idx, row in enumerate(page["rows"]):
                self.grid.insert("", "end", iid=str(idx), values=[row.get(col, "") for col in columns])
            start = page["offset"] + 1 if page["total"] else 0
            end = min(page["offset"] + len(page["rows"]), page["total"])
            self.page_label.configure(text=f"{start:,}–{end:,} of {page['total']:,}  ·  {table}")
            self.set_status(f"{self.host_var.get()}:{Path(str(path)).name} / {table} · {page['total']:,} rows")

        self._bg(work, done)

    def _sort_by(self, column: str) -> None:
        if self.sort_column == column:
            self.sort_desc = not self.sort_desc
        else:
            self.sort_column = column
            self.sort_desc = False
        self.load_page(reset=True)

    def prev_page(self) -> None:
        try:
            limit = int(self.page_limit.get())
        except (TypeError, ValueError):
            limit = 100
        self.page_offset = max(0, self.page_offset - limit)
        self.load_page()

    def next_page(self) -> None:
        try:
            limit = int(self.page_limit.get())
        except (TypeError, ValueError):
            limit = 100
        self.page_offset += limit
        self.load_page()

    def export_csv(self) -> None:
        if not self.current_db or not self.current_table:
            return
        dest = filedialog.asksaveasfilename(
            parent=self,
            title="Export CSV",
            defaultextension=".csv",
            initialfile=f"{self.current_table}.csv",
            filetypes=[("CSV", "*.csv")],
        )
        if not dest:
            return
        path = self.current_db
        table = self.current_table
        search = self.search_var.get()

        def work():
            return self.store.export_csv(path, table, Path(dest), search=search)

        def done(n):
            self.set_status(f"Exported {n:,} rows to {dest}")
            messagebox.showinfo("Export", f"Wrote {n:,} rows to {dest}", parent=self)

        self._bg(work, done)

    def delete_selected(self) -> None:
        if not self.current_db or not self.current_table:
            return
        selected = self.grid.selection()
        if not selected:
            return
        rows = [self.raw_rows[int(iid)] for iid in selected if iid.isdigit() and int(iid) < len(self.raw_rows)]
        if not rows:
            return
        if not messagebox.askyesno(
            "Delete rows",
            f"Delete {len(rows)} row(s) from {self.current_table} on the remote host?",
            parent=self,
        ):
            return
        try:
            n = self.store.delete_rows(self.current_db, self.current_table, rows)
        except Exception as exc:
            self._fail(exc)
            return
        self.set_status(f"Deleted {n} row(s)")
        self.refresh_tables()
        self.load_page()

    def run_sql(self) -> None:
        if not self.current_db:
            messagebox.showinfo("SQL", "Select a database first.", parent=self)
            return
        sql = self.sql_text.get("1.0", "end").strip()
        if not sql:
            return
        first = sql.split(None, 1)[0].lower()
        if first in WRITE_START:
            if not messagebox.askyesno(
                "Run SQL",
                f"This statement starts with {first.upper()} and will run on the remote host. Continue?",
                parent=self,
            ):
                return
        path = self.current_db

        def work():
            return self.store.execute_sql(path, sql)

        def done(result):
            if result["kind"] == "write":
                self.sql_grid.delete(*self.sql_grid.get_children())
                self.set_status(f"Write complete · rowcount {result['rowcount']}")
                self.refresh_tables()
                return
            columns = result["columns"]
            self.sql_grid.delete(*self.sql_grid.get_children())
            self.sql_grid["columns"] = columns
            for name in columns:
                self.sql_grid.heading(name, text=name)
                self.sql_grid.column(name, width=max(90, min(220, 10 * len(name) + 40)), stretch=False)
            for idx, row in enumerate(result["rows"]):
                self.sql_grid.insert("", "end", iid=str(idx), values=[row.get(col, "") for col in columns])
            note = " (truncated)" if result.get("truncated") else ""
            self.set_status(f"{len(result['rows'])} row(s){note}")

        self._bg(work, done)

    def _log_maint(self, text: str) -> None:
        self.maint_log.configure(state="normal")
        self.maint_log.insert("end", text.rstrip() + "\n")
        self.maint_log.see("end")
        self.maint_log.configure(state="disabled")

    def _need_db(self) -> str | None:
        if not self.current_db:
            messagebox.showinfo("Database", "Select a database first.", parent=self)
            return None
        return self.current_db

    def integrity(self) -> None:
        path = self._need_db()
        if not path:
            return

        def done(result):
            self._log_maint(f"integrity_check: {result}")
            self.set_status(f"Integrity: {result}")

        self._bg(lambda: self.store.integrity(path), done)

    def checkpoint(self) -> None:
        path = self._need_db()
        if not path:
            return

        def done(result):
            self._log_maint(f"wal_checkpoint: {result}")
            self.set_status(result)
            self.refresh_databases(select=path)

        self._bg(lambda: self.store.checkpoint(path), done)

    def vacuum(self) -> None:
        path = self._need_db()
        if not path:
            return
        if not messagebox.askyesno("Vacuum", f"Vacuum {Path(str(path)).name} on the remote host? This can take a while.", parent=self):
            return

        def done(_result):
            self._log_maint("VACUUM complete")
            self.set_status(f"Vacuumed {Path(str(path)).name} on {self.host_var.get()}")
            self.refresh_databases(select=path)

        self._bg(lambda: self.store.vacuum(path), done)

    def backup(self) -> None:
        path = self._need_db()
        if not path:
            return
        dest = filedialog.asksaveasfilename(
            parent=self,
            title="Download compact backup",
            defaultextension=".db",
            initialdir=str(DATA_DIR),
            initialfile=f"{Path(str(path)).stem}-backup.db",
            filetypes=[("SQLite", "*.db")],
        )
        if not dest:
            return

        def done(_result):
            self._log_maint(f"Backup written to {dest}")
            self.set_status(f"Backup saved: {dest}")
            messagebox.showinfo("Backup", f"Saved compact copy to {dest}", parent=self)

        self._bg(lambda: self.store.backup(path, Path(dest)), done)

    def restore(self) -> None:
        path = self._need_db()
        if not path:
            return
        source = filedialog.askopenfilename(
            parent=self,
            title="Replace database from file",
            initialdir=str(DATA_DIR),
            filetypes=[("SQLite", "*.db *.sqlite *.sqlite3")],
        )
        if not source:
            return
        if not messagebox.askyesno(
            "Replace remote database",
            f"Replace {Path(str(path)).name} on the remote host with {Path(source).name}?\n"
            "A timestamped copy is kept on the server and the web container restarts.",
            parent=self,
        ):
            return

        def work():
            return self.store.replace_with(path, Path(source))

        def done(result):
            self._log_maint(f"Replaced {path} from {source}: {result}")
            self.refresh_databases(select=path)

        self._bg(work, done)


def main() -> None:
    app = DatabaseApp()
    app.mainloop()
