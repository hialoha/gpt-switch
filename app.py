"""Tk GUI and background monitor. All widget operations stay on the UI thread."""
import concurrent.futures
import os
from pathlib import Path
import time
import threading
import json
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import sync_core as core
import discovery
import coverage
import occupancy


class App:
    def __init__(self, root, auto_discover=True):
        self.root = root
        root.title('GPT Switch 2.5 · Codex 对话模型同步')
        root.geometry('1080x760')
        root.minsize(980, 700)
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self.future = None
        self.job=('idle',False,None)
        self.last_backup = None
        self.next_check = 0
        self.last_status = ''
        self.discovered = None
        self.discovery_key = None
        self.discovery_error = ''
        self.pending_action = None
        self.discovered_at = 0
        self.cancel_event = threading.Event()
        self.closing = False
        self.last_audit = None
        self.ssh_window=None
        self.home = tk.StringVar(value=os.environ.get('CODEX_HOME', str(Path.home()/'.codex')))
        self.db = tk.StringVar()
        self.model = tk.StringVar()
        self.provider = tk.StringVar()
        self.auto = tk.BooleanVar()
        self.manual = tk.BooleanVar()
        self.status = tk.StringVar(value='就绪')
        body = ttk.Frame(root, padding=16); body.pack(fill='both', expand=True)
        ttk.Label(body, text='Codex 对话模型同步', font=('Microsoft YaHei UI', 18, 'bold')).pack(anchor='w')
        ttk.Label(body, text='同步本地对话（含已归档对话，跳过内部自动审核任务）。每次写入前自动备份。').pack(anchor='w', pady=(4,12))
        paths = ttk.Frame(body); paths.pack(fill='x')
        paths.columnconfigure(1, weight=1)
        for i, (label, var, command) in enumerate([
            ('Codex 目录', self.home, self.choose_home), ('数据库', self.db, self.choose_db)]):
            ttk.Label(paths, text=label).grid(row=i, column=0, sticky='w', pady=4)
            ttk.Entry(paths, textvariable=var).grid(row=i, column=1, sticky='ew', padx=8)
            ttk.Button(paths, text='选择…', command=command).grid(row=i, column=2)
        target = ttk.LabelFrame(body, text='当前同步目标', padding=10); target.pack(fill='x', pady=12)
        ttk.Checkbutton(target, text='自选目标（取消后跟随 Codex 默认值，不改全局配置）', variable=self.manual, command=self.load_config).pack(anchor='w')
        line = ttk.Frame(target); line.pack(fill='x', pady=8)
        ttk.Label(line, text='模型').pack(side='left')
        self.model_entry = ttk.Combobox(line, textvariable=self.model, width=26); self.model_entry.pack(side='left', padx=8)
        self.model_entry.bind('<<ComboboxSelected>>', self.select_model)
        ttk.Label(line, text='供应商').pack(side='left')
        self.provider_entry = ttk.Entry(line, textvariable=self.provider, width=18); self.provider_entry.pack(side='left', padx=8)
        self.refresh_button = ttk.Button(line, text='刷新 Codex 模型 / 默认值', command=self.refresh_models)
        self.refresh_button.pack(side='left')
        ttk.Button(target, text='从已有对话选择目标…', command=self.choose_reference).pack(anchor='w', pady=(0,4))
        self.config_note = ttk.Label(target, wraplength=900); self.config_note.pack(anchor='w')
        self.catalog_note = ttk.Label(target, wraplength=900); self.catalog_note.pack(anchor='w', pady=(4,0))
        bar = ttk.Frame(body); bar.pack(fill='x', pady=(0,10))
        self.actions = []
        for text, command in [('预览修改', self.preview), ('立即同步全部', self.apply), ('打开备份目录', self.open_backups), ('最小化到后台', root.iconify)]:
            button = ttk.Button(bar, text=text, command=command); button.pack(side='left', padx=(0,8)); self.actions.append(button)
        ttk.Checkbutton(body, text='后台自动监控：每 3 秒读取配置，Codex 退出后自动备份并同步', variable=self.auto, command=self.toggle_monitor).pack(anchor='w')
        tools=ttk.Frame(body); tools.pack(fill='x',pady=(4,10))
        ttk.Button(tools,text='查看扫描详情',command=self.show_audit).pack(side='left')
        ttk.Button(tools,text='诊断数据库占用',command=self.show_occupancy).pack(side='left',padx=8)
        ttk.Button(tools,text='SSH 远端同步…',command=self.open_ssh).pack(side='left',padx=8)
        ttk.Label(tools,text='右上角 X 会真正退出；仅“最小化到后台”会保留监控。').pack(side='left')
        frame = ttk.Frame(body); frame.pack(fill='both', expand=True)
        columns = ('title','old_model','old_provider','new_model','new_provider')
        self.table = ttk.Treeview(frame, columns=columns, show='headings', height=8)
        for key, title in zip(columns, ['对话 / ID','原模型','原供应商','目标模型','目标供应商']):
            self.table.heading(key,text=title); self.table.column(key,width=160)
        scroll = ttk.Scrollbar(frame, orient='vertical', command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set); scroll.pack(side='right',fill='y'); self.table.pack(fill='both',expand=True)
        self.log = tk.Text(body, height=5, state='disabled', wrap='word'); self.log.pack(fill='x', pady=8)
        foot = ttk.Frame(body); foot.pack(fill='x')
        ttk.Label(foot, textvariable=self.status, wraplength=780).pack(side='left')
        ttk.Button(foot, text='退出工具', command=self.quit).pack(side='right')
        root.protocol('WM_DELETE_WINDOW', self.close)
        self.choose_initial_db(); self.load_config()
        if auto_discover: root.after(100, self.refresh_models)
        root.after(200, self.tick)

    def report(self, text):
        self.status.set(text)
        if text == self.last_status: return
        self.last_status = text
        self.log.configure(state='normal'); self.log.insert('end',time.strftime('%H:%M:%S ')+text+'\n'); self.log.see('end'); self.log.configure(state='disabled')

    def choose_initial_db(self):
        try: self.db.set(str(core.database(self.home.get())))
        except Exception as e: self.db.set(''); self.report(str(e))

    def choose_home(self):
        selected = filedialog.askdirectory(initialdir=self.home.get())
        if selected:
            self.auto.set(False); self.home.set(selected); self.discovered = None
            self.choose_initial_db(); self.load_config(); self.refresh_models()

    def choose_db(self):
        selected = filedialog.askopenfilename(initialdir=self.home.get(), filetypes=[('SQLite','*.sqlite')])
        if selected: self.auto.set(False); self.db.set(selected)

    def select_model(self, _event=None):
        self.manual.set(True)
        self.load_config()

    def refresh_models(self):
        if self.future: return
        self.load_config()
        home = self.home.get()
        try: key = discovery.fingerprint(home)
        except Exception as e:
            self.report('无法读取配置：'+str(e)); return
        self.job = ('discover', False, (key,))
        self.future = self.pool.submit(discovery.discover, home, cancel_event=self.cancel_event)
        self.refresh_button.configure(state='disabled')
        for b in self.actions[:2]: b.configure(state='disabled')
        self.report('正在通过官方 Codex 识别模型和默认值…')

    def choose_reference(self):
        try:
            c = core.connection(Path(self.db.get()))
            try: candidates = [r for r in core.rows(c) if r['model'] and r['model'] != 'codex-auto-review']
            finally: c.close()
            dialog = tk.Toplevel(self.root); dialog.title('选择一条你确认能正常使用的对话'); dialog.geometry('800x420')
            table = ttk.Treeview(dialog, columns=('title','model','provider'),show='headings')
            for key, title in [('title','对话'),('model','模型'),('provider','供应商')]: table.heading(key,text=title)
            scrollbar = ttk.Scrollbar(dialog, orient='vertical', command=table.yview)
            scrollbar.pack(side='right',fill='y'); table.configure(yscrollcommand=scrollbar.set)
            table.pack(fill='both',expand=True)
            for i,r in enumerate(candidates): table.insert('','end',iid=str(i),values=(r['title'] or r['id'],r['model'],r['model_provider']))
            def select(_event=None):
                chosen = table.selection()
                if not chosen: return
                r = candidates[int(chosen[0])]
                self.manual.set(True); self.model.set(r['model']); self.provider.set(r['model_provider']); self.load_config()
                self.report('已采用所选对话的模型和供应商；可预览修改。'); dialog.destroy()
            ttk.Button(dialog,text='采用选中对话的设置',command=select).pack(pady=8)
            table.bind('<Double-1>',select)
        except Exception as e: messagebox.showerror('无法读取对话',str(e))

    def load_config(self):
        editable = self.manual.get()
        self.model_entry.configure(state='normal' if editable else 'readonly')
        self.provider_entry.configure(state='normal' if editable else 'readonly')
        self.catalog_error = None
        try:
            values, path = core.catalog(self.home.get())
            key = discovery.fingerprint(self.home.get())
            found = self.discovered if self.discovery_key == key else None
            if not path:
                values = found['values'] if found else discovery.cached_models(self.home.get())
            self.model_entry.configure(values=values)
            self.catalog_note.configure(text=f'自定义目录：{path}（{len(values)} 项）' if path else (
                f'官方模型列表：{len(values)} 项。可直接下拉选择；无需配置 model_catalog_json。' if found else
                f'离线候选列表：{len(values)} 项；正在等待官方默认值识别，可先下拉选择或从已有对话选择。'))
        except Exception as e:
            self.catalog_error = str(e)
            self.model_entry.configure(values=())
            self.catalog_note.configure(text='模型目录加载失败：'+str(e))
        if editable:
            self.config_note.configure(text='使用自选目标；取消勾选后恢复跟随 Codex 默认值。'); return
        try:
            model, provider, profile = core.global_target(self.home.get())
            found = self.discovered if self.discovery_key == discovery.fingerprint(self.home.get()) else None
            if found:
                model, provider = found['model'], found['provider']
            self.model.set(model); self.provider.set(provider)
            self.config_note.configure(text=(found['source'] if found else
                ('目标来自 config.toml' + ('，profile: '+profile if profile else '') if model else
                 '未显式设置 model 是正常情况；将通过 Codex 自动获取默认值。')))
            if self.discovery_error:
                self.catalog_note.configure(text='自动识别失败：'+self.discovery_error+' 可下拉选择，或从已有对话选择目标。')
        except Exception as e:
            self.model.set(''); self.provider.set(''); self.config_note.configure(text='配置错误：'+str(e))

    def target(self):
        self.load_config()
        if self.catalog_error: raise ValueError(self.catalog_error)
        model, provider = self.model.get().strip(), self.provider.get().strip()
        if not model or not provider: raise ValueError('尚未识别默认模型。请点击“刷新 Codex 模型 / 默认值”，或直接下拉选择模型、从已有对话选择目标。')
        if not self.db.get(): raise ValueError('请先选择数据库。')
        return Path(self.db.get()), model, provider

    def submit(self, mode, automatic=False):
        if self.future: return
        try:
            if not self.manual.get() and self.discovery_key != discovery.fingerprint(self.home.get()):
                self.pending_action = (mode,automatic)
                self.refresh_models()
                return
            args = self.target()
            home=self.home.get()
            if mode == 'apply':
                def work():
                    count, backup = core.sync(*args, cancel_event=self.cancel_event)
                    return count, backup, core.preview(*args)
            else:
                def work(): return core.preview(*args), coverage.audit(home,*args,cancel_event=self.cancel_event)
            self.job = (mode, automatic, args)
            self.future = self.pool.submit(work)
            for b in self.actions[:2]: b.configure(state='disabled')
            if not automatic: self.report('正在备份并同步…' if mode == 'apply' else '正在只读预览…')
        except Exception as e:
            self.report(str(e))
            if automatic:
                self.auto.set(False); self.report('自动监控已暂停：'+str(e))
            else: messagebox.showerror('无法执行',str(e))

    def preview(self): self.submit('preview')
    def apply(self): self.submit('apply')

    def show_rows(self, rows, args):
        self.table.delete(*self.table.get_children())
        for r in rows:
            self.table.insert('', 'end', values=(r['title'] or r['id'], r['model'],r['model_provider'],args[1],args[2]))

    def toggle_monitor(self):
        self.next_check = 0
        self.report('已开启后台监控；只有 Codex 退出后才自动修改。' if self.auto.get() else '后台监控已关闭。')

    def tick(self):
        if self.closing:
            if not self.future or self.future.done():
                self.pool.shutdown(wait=False,cancel_futures=True); self.root.destroy(); return
            self.root.after(100,self.tick); return
        if self.future and self.future.done():
            future = self.future; self.future = None
            mode, automatic, args = self.job
            self.refresh_button.configure(state='normal')
            for b in self.actions[:2]: b.configure(state='normal')
            try:
                result = future.result()
                if mode == 'remote': args(result)
                elif mode == 'discover':
                    self.discovered = result; self.discovery_key = args[0]; self.discovery_error = ''
                    self.discovered_at = time.monotonic()
                    self.load_config()
                    self.report('已识别目标：'+(result['model'] or '请从下拉框选择')+' / '+result['provider']+'。可点击预览修改。')
                    if self.pending_action:
                        action = self.pending_action; self.pending_action = None
                        self.submit(*action)
                elif mode == 'apply':
                    count, backup, rows = result
                    if backup: self.last_backup = backup
                    self.show_rows(rows,args)
                    self.report(f'同步完成：{count} 条。' + (f'备份：{backup}' if backup else '所有对话已一致。'))
                else:
                    rows,self.last_audit=result
                    self.show_rows(rows,args); self.report(coverage.summary(self.last_audit)+' 尚未写入。')
            except Exception as e:
                self.report('失败：'+str(e))
                if mode == 'remote': args(None,e)
                elif mode == 'discover':
                    self.pending_action = None
                    self.auto.set(False)
                    self.discovery_error = str(e); self.load_config()
                elif automatic:
                    self.auto.set(False); self.report('自动监控已暂停：'+str(e)+'。处理后重新勾选以重试。')
                else: messagebox.showerror('操作失败',str(e))
            self.next_check = time.monotonic() + 3
        if self.auto.get() and not self.future and time.monotonic() >= self.next_check:
            self.next_check = time.monotonic()+3
            try:
                if core.running(Path(self.db.get())):
                    self.load_config(); self.report('监控中：所选数据库仍有进程占用，点击“诊断数据库占用”查看具体 PID。')
                elif not self.manual.get() and (self.discovery_key != discovery.fingerprint(self.home.get()) or time.monotonic()-self.discovered_at > 300):
                    self.refresh_models()
                else: self.submit('apply', automatic=True)
            except Exception as e:
                self.auto.set(False); self.report('监控暂停：'+str(e))
        self.root.after(500, self.tick)

    def open_backups(self):
        folder = self.last_backup or (Path(self.db.get()).parent/'gpt-switch-backups')
        if folder.exists(): os.startfile(str(folder))
        else: messagebox.showinfo('备份', '尚无备份；首次实际同步时自动创建。')

    def show_audit(self):
        if not self.last_audit:
            messagebox.showinfo('扫描详情','先点击“预览修改”，将扫描数据库、会话文件及本地/远程目录索引。'); return
        dialog=tk.Toplevel(self.root); dialog.title('扫描覆盖详情（只读）'); dialog.geometry('900x620')
        text=tk.Text(dialog,wrap='word')
        bar=ttk.Scrollbar(dialog,orient='vertical',command=text.yview); bar.pack(side='right',fill='y')
        text.configure(yscrollcommand=bar.set); text.pack(fill='both',expand=True)
        text.insert('end',coverage.summary(self.last_audit)+'\n\n未入库的会话、其他数据库和远程索引不会被静默改写。远程对话须在对应主机处理。\n\n'+json.dumps(self.last_audit,ensure_ascii=False,indent=2))
        text.configure(state='disabled')

    def show_occupancy(self):
        try:
            items=occupancy.holders(Path(self.db.get()))
            messagebox.showinfo('所选数据库实际占用者',occupancy.describe(items) if items else '未发现其他进程持有所选数据库及 WAL/SHM。无关的 codex.exe 不再阻止同步。')
        except Exception as e: messagebox.showerror('占用检测失败',str(e))

    def close(self): self.quit()

    def open_ssh(self):
        from remote_ui import RemoteWindow
        if self.ssh_window and self.ssh_window.root.winfo_exists(): self.ssh_window.root.lift()
        else: self.ssh_window=RemoteWindow(self)

    def remote_job(self,work,callback):
        if self.future: return
        self.job=('remote',False,callback)
        self.future=self.pool.submit(work)
        for b in self.actions[:2]: b.configure(state='disabled')

    def quit(self):
        self.auto.set(False); self.pending_action=None; self.closing=True; self.cancel_event.set()
        if self.future and not self.future.done():
            self.future.cancel()
            self.report('正在安全退出：SSH 只读操作会取消，已发送的远端写入将等待事务结果…' if self.job[0]=='remote' else '正在退出：取消后台操作并回滚未提交修改…')
            self.root.after(100,self.tick)
        else:
            self.pool.shutdown(wait=False,cancel_futures=True); self.root.destroy()


if __name__ == '__main__':
    root = tk.Tk(); App(root); root.mainloop()
