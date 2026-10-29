import json
import time
import tkinter as tk
from tkinter import ttk,messagebox
import ssh_remote


class RemoteWindow:
    def __init__(self,parent):
        self.parent=parent
        self.root=tk.Toplevel(parent.root); self.root.title('GPT Switch 2.5 · SSH 远端对话同步'); self.root.geometry('1100x820')
        body=ttk.Frame(self.root,padding=14); body.pack(fill='both',expand=True)
        ttk.Label(body,text='SSH 远端对话同步',font=('Microsoft YaHei UI',16,'bold')).pack(anchor='w')
        ttk.Label(body,text='操作在选中的远端主机执行；备份留在远端数据库旁。不会修改本机的对话目录索引。').pack(anchor='w',pady=8)
        self.host=tk.StringVar(); self.home=tk.StringVar(); self.db=tk.StringVar()
        self.model=tk.StringVar(value=parent.model.get()); self.provider=tk.StringVar(value=parent.provider.get())
        self.target_source=tk.StringVar(value='本机界面目标（复制）；检查远端不会覆盖此目标。')
        self.remote_info=tk.StringVar(value='尚未检查远端客户端。远端客户端的推荐默认值不代表全局最新模型。')
        self.remote_result=None; self.setting_target=False
        self.auto=tk.BooleanVar(); self.preview_key=None; self.next_check=0
        self.host_revision=0; self.last_host=''; self.holder_result=None; self.holder_context=None
        self.repair_plan=None
        grid=ttk.Frame(body); grid.pack(fill='x'); grid.columnconfigure(1,weight=1)
        for i,(name,var) in enumerate([('SSH 主机',self.host),('远端 Codex 目录（空=默认）',self.home),('远端数据库（空=自动）',self.db),('目标模型',self.model),('目标供应商',self.provider)]):
            ttk.Label(grid,text=name).grid(row=i,column=0,sticky='w',pady=4)
            if i==0:
                values=ssh_remote.aliases(); entry=ttk.Combobox(grid,textvariable=var,values=values)
            elif i==3:
                self.model_entry=ttk.Combobox(grid,textvariable=var,values=self.local_values())
                entry=self.model_entry
            else: entry=ttk.Entry(grid,textvariable=var)
            entry.grid(row=i,column=1,sticky='ew',padx=8)
        ttk.Label(body,textvariable=self.target_source,wraplength=980).pack(anchor='w',pady=6)
        ttk.Label(body,textvariable=self.remote_info,wraplength=980).pack(anchor='w',pady=(0,6))
        for var in (self.model,self.provider): var.trace_add('write',self.target_edited)
        bar=ttk.Frame(body); bar.pack(fill='x')
        ttk.Button(bar,text='一键修复远端…',command=lambda:self.run('plan')).pack(side='left',padx=(0,6))
        ttk.Button(bar,text='使用本机当前目标',command=self.adopt_local).pack(side='left',padx=(0,6))
        for label,action in [('检查远端模型','discover'),('预览远端全部对话','preview'),('备份并同步远端','apply'),('诊断远端占用','occupancy')]:
            ttk.Button(bar,text=label,command=lambda a=action:self.run(a)).pack(side='left',padx=(0,6))
        extra=ttk.Frame(body); extra.pack(fill='x',pady=4)
        self.adopt_button=ttk.Button(extra,text='采用上面检查到的远端默认值（会改变目标）',command=self.adopt_remote,state='disabled')
        self.adopt_button.pack(side='left')
        self.stop_button=ttk.Button(extra,text='结束已诊断的远端占用进程…',command=self.choose_stop,state='disabled')
        self.stop_button.pack(side='left',padx=8)
        ttk.Checkbutton(body,text='监控此 SSH 主机：预览后每 10 秒检查占用，释放后自动备份并同步当前目标',variable=self.auto).pack(anchor='w',pady=8)
        columns=('title','model','provider')
        self.table=ttk.Treeview(body,columns=columns,show='headings',height=10)
        for key,label in zip(columns,('对话 / ID','原模型','原供应商')):
            self.table.heading(key,text=label); self.table.column(key,width=300)
        self.table.pack(fill='both',expand=True)
        self.log=tk.Text(body,height=9,wrap='word',state='disabled'); self.log.pack(fill='x',pady=8)
        self.status=tk.StringVar(value='请选择主机，然后预览。需要 OpenSSH 已配置好免交互登录。')
        ttk.Label(body,textvariable=self.status,wraplength=980).pack(anchor='w')
        self.root.protocol('WM_DELETE_WINDOW',self.close)
        self.root.after(1000,self.tick)
        self.host.trace_add('write',self.host_changed)

    def key(self): return tuple(v.get().strip() for v in (self.host,self.home,self.db,self.model,self.provider))

    def host_changed(self,*_):
        host=self.host.get().strip()
        if host==self.last_host: return
        self.last_host=host; self.host_revision+=1
        self.home.set(''); self.db.set(''); self.preview_key=None; self.remote_result=None
        self.holder_result=None; self.holder_context=None; self.auto.set(False)
        self.repair_plan=None
        self.remote_info.set('已切换主机；远端目录和数据库已清空，等待重新检测。')
        self.adopt_button.configure(state='disabled'); self.stop_button.configure(state='disabled')
        self.model_entry.configure(values=self.local_values())
        self.table.delete(*self.table.get_children())
        self.log.configure(state='normal'); self.log.delete('1.0','end'); self.log.configure(state='disabled')
        self.report('已选择 '+host+'；只保留目标模型，已清除上一主机的路径和结果。')

    def context(self):
        return self.host_revision,self.host.get().strip(),self.home.get().strip(),self.db.get().strip()

    def confirm_repair(self,result):
        if not result['rows']:
            self.report('远端已全部匹配当前目标，无需修改，也不会结束进程。'); return
        if any(not r.get('terminable') for r in result['holders']):
            self.report('有非 Codex 程序持有数据库，不能自动结束：'+json.dumps(result['holders'],ensure_ascii=False)); return
        dialog=tk.Toplevel(self.root); dialog.title('远端修复预览'); dialog.geometry('900x440')
        context=self.context(); key=self.key()
        self.repair_plan=(key,context,result)
        text=tk.Text(dialog,wrap='word',height=14); text.pack(fill='both',expand=True,padx=12,pady=12)
        message=f"主机：{self.host.get()}\n数据库：{self.db.get()}\n目标：{self.model.get()} / {self.provider.get()}\n将修改 {len(result['rows'])} 条对话。\n\n将先备份数据库，再停止以下占用进程、同步、复核：\n"
        message+='\n'.join(f"PID {r['pid']} · {r['path']}" for r in result['holders']) if result['holders'] else '当前没有占用进程，无需停止任何进程。'
        message+='\n\n这些后端承载的其他对话可能中断。不会停止父进程、VS Code 或自动重启后的新 PID。'
        text.insert('end',message); text.configure(state='disabled')
        force=tk.BooleanVar(value=False)
        ttk.Checkbutton(dialog,text='正常退出无效时，允许强制结束上面列出的同一批进程',variable=force).pack(anchor='w',padx=12)
        def commit():
            if key!=self.key() or context!=self.context():
                dialog.destroy(); self.report('主机或目标变化，请重新点击一键修复。'); return
            self.run('repair',targets=result['holders'],force=force.get(),plan_hash=result['plan_hash']); dialog.destroy()
        ttk.Button(dialog,text='开始：备份 → 停止占用 → 同步 → 复核',command=commit).pack(pady=12)

    def choose_stop(self):
        if not self.holder_result or self.holder_context!=self.context():
            self.report('主机或路径已变化，请先重新诊断远端占用。'); return
        items=[r for r in self.holder_result['holders'] if r.get('terminable')]
        if not items: self.report('没有可结束的同用户 Codex 占用进程。'); return
        dialog=tk.Toplevel(self.root); dialog.title('选择要结束的远端 Codex 进程'); dialog.geometry('920x420')
        ttk.Label(dialog,text='仅列实际占用所选数据库的 Codex。会先备份数据库，再结束选中进程；对应对话可能中断。',wraplength=880).pack(pady=8)
        table=ttk.Treeview(dialog,columns=('pid','name','path'),show='headings',selectmode='extended')
        for key,label in [('pid','PID'),('name','进程'),('path','可执行路径')]: table.heading(key,text=label)
        table.column('pid',width=70); table.column('name',width=100); table.column('path',width=650)
        for i,r in enumerate(items): table.insert('','end',iid=str(i),values=(r['pid'],r['name'],r['path']))
        table.pack(fill='both',expand=True)
        force=tk.BooleanVar()
        ttk.Checkbutton(dialog,text='强制结束（SIGKILL）；默认使用正常退出信号 SIGTERM',variable=force).pack(pady=8)
        context=self.context()
        def stop():
            selected=[items[int(i)] for i in table.selection()]
            if not selected: return
            if context!=self.context(): dialog.destroy(); self.report('主机或路径已变化，请重新诊断。'); return
            # The selected PIDs, host and signal are concrete before this action is offered.
            self.run('terminate',targets=selected,force=force.get()); dialog.destroy()
        ttk.Button(dialog,text='备份并结束选中进程',command=stop).pack(pady=8)

    def local_values(self):
        try: return list(self.parent.model_entry.cget('values'))
        except (AttributeError,TypeError): return []

    def target_edited(self,*_):
        if not self.setting_target:
            self.target_source.set('目标来源：手动选择/输入。需重新预览后才可同步。')
            self.preview_key=None; self.auto.set(False)

    def set_target(self,model,provider,source):
        self.setting_target=True
        try: self.model.set(model); self.provider.set(provider)
        finally: self.setting_target=False
        self.target_source.set('目标来源：'+source+' → '+model+' / '+provider)
        self.preview_key=None; self.auto.set(False)

    def adopt_local(self):
        model=self.parent.model.get().strip(); provider=self.parent.provider.get().strip()
        if not model or not provider:
            self.report('本机尚未识别目标，请先在主窗口刷新模型。'); return
        self.set_target(model,provider,'本机当前目标')
        self.report('已恢复本机目标，请重新预览远端对话。')

    def adopt_remote(self):
        if not self.remote_result: return
        result,context=self.remote_result
        if context!=(self.host.get().strip(),self.home.get().strip(),self.db.get().strip()):
            self.report('主机或路径已经变化，请重新检查远端模型。'); return
        if not result['model'] or not result['provider']:
            self.report('远端未返回有效默认值，请使用本机目标或手动选择。'); return
        self.set_target(result['model'],result['provider'],'明确采用的远端客户端默认值')
        self.report('已采用远端值；请核对目标模型并重新预览。')

    def report(self,text):
        if not self.root.winfo_exists(): return
        self.status.set(text); self.log.configure(state='normal'); self.log.insert('end',time.strftime('%H:%M:%S ')+text+'\n'); self.log.see('end'); self.log.configure(state='disabled')

    def run(self,action,automatic=False,targets=None,force=False,plan_hash=None):
        if self.parent.future:
            if not automatic: self.report('另一项操作正在运行，请等待完成。')
            return
        host,home,db,model,provider=self.key()
        if not host: self.report('请选择 SSH 主机'); return
        if action in ('preview','apply','plan','repair') and (not model or not provider):
            self.report('请先读取默认值或填写目标模型和供应商。'); self.auto.set(False); return
        if action=='apply' and self.preview_key!=self.key():
            self.report('主机、数据库或目标已变化，请先重新预览，再同步。'); self.auto.set(False); return
        if action=='terminate':
            if self.holder_context!=self.context() or not targets:
                self.report('请先重新诊断并选择占用进程。'); return
            self.auto.set(False); self.preview_key=None
        if action=='repair':
            if not self.repair_plan or self.repair_plan[0]!=self.key() or self.repair_plan[1]!=self.context():
                self.report('请先重新生成一键修复预览。'); return
            self.auto.set(False); self.preview_key=None
        snapshot=self.key()
        revision=self.host_revision
        self.report('SSH '+host+'：'+action+' 进行中…')
        def work():
            return ssh_remote.request(host,action,model,provider,home,db,cancel_event=self.parent.cancel_event,targets=targets,force=force,plan_hash=plan_hash)
        def finish(result,error=None):
            if not self.root.winfo_exists(): return
            if revision!=self.host_revision:
                self.report('已忽略上一主机 '+host+' 的返回结果；当前主机路径保持为空或当前填写值。'); return
            if error:
                self.auto.set(False); self.report('失败：'+str(error)); return
            # Never apply a response for a host the user has changed while it ran.
            if snapshot!=self.key() or revision!=self.host_revision:
                self.auto.set(False); self.preview_key=None
                self.report('原主机 '+host+' 操作已返回，但界面选项已变化；请重新预览。结果：'+json.dumps(result if action=='apply' else {'db':result.get('db')},ensure_ascii=False)); return
            self.home.set(result['home']); self.db.set(result['db'])
            for warning in result.get('warnings',[]): self.report(warning)
            if action=='discover':
                self.remote_result=(result,(self.host.get().strip(),self.home.get().strip(),self.db.get().strip()))
                self.model_entry.configure(values=list(dict.fromkeys(self.local_values()+result.get('values',[]))))
                client=result.get('client',{})
                note=f"远端检查：{result['model']} / {result['provider']}；{client.get('version','版本未知')}；{client.get('path','路径未知')}。当前同步目标保持不变。"
                if result['provider']!=self.provider.get().strip():
                    note+=' 远端供应商与当前同步目标不同，未采用。'
                self.remote_info.set(note); self.adopt_button.configure(state='normal')
                self.report(note)
                for item in result.get('clients',[]): self.report('发现客户端：'+item['path']+' / '+item['version'])
            elif action in ('preview','plan'):
                self.preview_key=self.key()
                self.table.delete(*self.table.get_children())
                for r in result['rows']: self.table.insert('','end',values=(r['title'] or r['id'],r['model'],r['model_provider']))
                audit=result['audit']
                self.report(f"{host} / {result['db']}：扫描 {audit['database_total']}，需修改 {len(result['rows'])}，已一致 {audit['already_matches']}，内部跳过 {audit['internal_skipped']}。目标：{model} / {provider}。")
                self.report('扫描详情：'+json.dumps(audit,ensure_ascii=False))
                self.status.set(f"预览目标：{model} / {provider}；将修改 {len(result['rows'])} 条。")
                if action=='plan': self.confirm_repair(result)
            elif action=='apply':
                self.auto.set(False); self.preview_key=None
                self.report(f"{host} 同步完成 {result['count']} 条。远端备份：{result['backup'] or '无变化，无需备份'}。请重新预览核对。")
            elif action=='occupancy':
                self.holder_result=result; self.holder_context=self.context()
                self.stop_button.configure(state='normal' if any(r.get('terminable') for r in result['holders']) else 'disabled')
                self.report('实际占用：'+json.dumps(result['holders'],ensure_ascii=False))
                if result.get('db_missing'):
                    self.auto.set(False); self.report('此主机当前用户未找到 Codex 数据库，请确认主机或手动填写路径。')
                elif automatic and not result['holders']: self.run('apply',automatic=True)
            elif action=='terminate':
                self.holder_result=None; self.holder_context=None; self.stop_button.configure(state='disabled')
                self.report('结束进程结果：'+json.dumps(result.get('sent',[]),ensure_ascii=False)+'；备份：'+str(result.get('backup')))
                self.report('当前剩余占用：'+json.dumps(result['holders'],ensure_ascii=False)+'。请重新诊断、预览后再同步。')
            elif action=='repair':
                self.auto.set(False); self.preview_key=None; self.repair_plan=None
                self.holder_result=None; self.holder_context=None; self.stop_button.configure(state='disabled')
                self.table.delete(*self.table.get_children())
                self.report(f"修复完成：已同步 {result['count']} 条，复核剩余不匹配 {result['verified_remaining']} 条。备份：{result['backup']}；停止进程前备份：{result.get('stop_backups',[])}")
            self.next_check=time.monotonic()+10
        self.parent.remote_job(work,finish)

    def tick(self):
        if self.auto.get() and not self.parent.future and time.monotonic()>=self.next_check:
            self.next_check=time.monotonic()+10
            if self.preview_key!=self.key():
                self.auto.set(False); self.report('先预览当前目标，再勾选远端监控。')
            else: self.run('occupancy',automatic=True)
        if not self.parent.closing: self.root.after(1000,self.tick)

    def close(self):
        self.auto.set(False)
        if self.parent.future and not self.parent.future.done() and self.parent.job[0]=='remote':
            self.report('远端操作尚未返回。关闭主窗口可取消只读操作；写入中会等待远端事务结果。'); return
        self.root.destroy()
