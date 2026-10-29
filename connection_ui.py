"""Connection sync UI. Secret bundles live only in memory and SSH stdin."""
import json
import time
import tkinter as tk
from tkinter import ttk,messagebox
import webbrowser
import connection_sync
import ssh_remote


class ConnectionWindow:
    def __init__(self,remote):
        self.remote=remote; self.app=remote.parent
        self.root=tk.Toplevel(remote.root); self.root.title('GPT Switch 2.6 · 跟随本机连接与登录态'); self.root.geometry('1040x780')
        self.host=remote.host.get().strip()
        self.home=tk.StringVar(value=remote.home.get()); self.db=tk.StringVar(value=remote.db.get())
        self.stop=tk.BooleanVar(value=True); self.force=tk.BooleanVar(value=False)
        self.source=tk.StringVar(value='等待读取本机 cc-switch 当前连接。')
        self.status=tk.StringVar(value='先预览差异，再同步。凭证只发到你选择的受信任 SSH 主机。')
        self.device=tk.StringVar(); self.login_id=''; self.url=''; self.next_poll=0
        self.bundle=None; self.plan=None; self.backup=''; self.revision=0; self.updating=False
        body=ttk.Frame(self.root,padding=14); body.pack(fill='both',expand=True)
        ttk.Label(body,text='本机 → '+self.host+'：连接配置与登录态',font=('Microsoft YaHei UI',16,'bold')).pack(anchor='w')
        ttk.Label(body,text='解决远端新对话仍走第三方 / 登录失效的问题。此入口不修改旧对话数据库字段。').pack(anchor='w',pady=6)
        for label,var in [('远端 Codex 目录（空=自动）',self.home),('远端数据库（可空，仅用于备份与占用检查）',self.db)]:
            line=ttk.Frame(body); line.pack(fill='x',pady=3)
            ttk.Label(line,text=label,width=45).pack(side='left'); ttk.Entry(line,textvariable=var).pack(side='left',fill='x',expand=True)
            var.trace_add('write',self.changed)
        ttk.Label(body,textvariable=self.source,wraplength=980).pack(anchor='w',pady=10)
        self.detail=tk.Text(body,height=12,wrap='word',state='disabled'); self.detail.pack(fill='both',expand=True)
        ttk.Checkbutton(body,text='停止预览中列出的此目录 Codex 后端（会中断其对话，完成后请重新连接 SSH 项目）',variable=self.stop).pack(anchor='w',pady=(8,3))
        ttk.Checkbutton(body,text='正常退出无效时允许强制停止同一批后端',variable=self.force).pack(anchor='w')
        bar=ttk.Frame(body); bar.pack(fill='x',pady=10)
        for text,command in [('预览本机 → 远端',self.preview),('备份并同步配置与登录态',self.apply),('验证远端账号 / 连接',lambda:self.action('connection_verify')),('恢复本次同步前配置',self.restore)]:
            ttk.Button(bar,text=text,command=command).pack(side='left',padx=(0,6))
        auth=ttk.LabelFrame(body,text='ChatGPT 仍报 workspace-401 时：远端独立重新授权',padding=10); auth.pack(fill='x')
        ttk.Label(auth,text='仅当同步到内置 OpenAI / ChatGPT 登录时使用。验证码不写入工具日志；设备码登录需账号允许。',wraplength=960).pack(anchor='w')
        line=ttk.Frame(auth); line.pack(fill='x',pady=6)
        ttk.Button(line,text='启动远端设备码授权',command=lambda:self.action('device_start')).pack(side='left')
        ttk.Button(line,text='打开官方授权页',command=self.open_url).pack(side='left',padx=6)
        ttk.Button(line,text='取消本次授权',command=lambda:self.action('device_cancel')).pack(side='left')
        ttk.Entry(auth,textvariable=self.device,state='readonly').pack(fill='x')
        self.log=tk.Text(body,height=5,wrap='word',state='disabled'); self.log.pack(fill='x',pady=8)
        ttk.Label(body,textvariable=self.status,wraplength=980).pack(anchor='w')
        self.root.protocol('WM_DELETE_WINDOW',self.close)
        self.timer=self.root.after(500,self.tick)
        self.root.bind('<Destroy>',self.destroyed)

    def destroyed(self,event):
        if event.widget==self.root:
            try: self.root.after_cancel(self.timer)
            except tk.TclError: pass
            self.bundle=None; self.plan=None

    def changed(self,*_):
        if not self.updating:
            self.revision+=1; self.plan=None; self.bundle=None; self.backup=''; self.login_id=''; self.device.set('')

    def report(self,text):
        self.status.set(text); self.log.configure(state='normal'); self.log.insert('end',time.strftime('%H:%M:%S ')+text+'\n'); self.log.see('end'); self.log.configure(state='disabled')

    def local_bundle(self):
        return connection_sync.build_bundle(self.app.home.get(),self.app.model.get(),self.app.provider.get())

    def queue(self,work,done):
        if self.app.future: self.report('请等待当前操作完成。'); return
        revision=self.revision
        def finish(result,error=None):
            if not self.root.winfo_exists(): return
            if revision!=self.revision:
                self.report('路径已改变，已忽略原请求；请重新预览。'); return
            if error:
                self.report('未完成：'+str(error)); return
            done(result)
        self.app.remote_job(work,finish)

    def preview(self):
        home=self.home.get().strip(); db=self.db.get().strip()
        local=(self.app.home.get(),self.app.model.get(),self.app.provider.get())
        def work():
            bundle=connection_sync.build_bundle(*local)
            plan=ssh_remote.request(self.host,'connection_plan',home=home,db=db,source_fingerprint=bundle['fingerprint'],cancel_event=self.app.cancel_event)
            return bundle,plan
        def finish(pair):
            self.bundle,self.plan=pair
            self.updating=True
            try: self.home.set(self.plan['home']); self.db.set(self.plan['db'])
            finally: self.updating=False
            summary=self.bundle['summary']; self.source.set('本机当前连接：'+json.dumps(summary,ensure_ascii=False))
            content='本机将同步：\n'+json.dumps(summary,ensure_ascii=False,indent=2)
            content+='\n\n远端当前：\n'+json.dumps(self.plan['before'],ensure_ascii=False,indent=2)
            content+='\n\n将停止的后端（启用上方选项时）：\n'+json.dumps(self.plan['targets'],ensure_ascii=False,indent=2)
            content+='\n\n仅同步必要连接字段与凭证；保留远端项目、MCP、插件及权限设置的语义。原文件完整备份，TOML 注释可能重新排版。凭证不显示、不写本机回执。'
            self.detail.configure(state='normal'); self.detail.delete('1.0','end'); self.detail.insert('end',content); self.detail.configure(state='disabled')
            self.report('预览已就绪。点击“备份并同步配置与登录态”将凭证通过 SSH 发往 '+self.host+'。')
        self.report('正在读取本机连接并比较远端配置（不传送密钥）…'); self.queue(work,finish)

    def apply(self):
        if not self.bundle or not self.plan: self.report('请先预览本机 → 远端。'); return
        bundle=self.bundle; plan=self.plan
        home=self.home.get().strip(); db=self.db.get().strip(); stop=self.stop.get(); force=self.force.get()
        local=(self.app.home.get(),self.app.model.get(),self.app.provider.get())
        def work():
            current=connection_sync.build_bundle(*local)
            if current['fingerprint']!=bundle['fingerprint']: raise ValueError('本机配置或登录态已变化，请重新预览。')
            return ssh_remote.request(self.host,'connection_apply',home=home,db=db,connection=current,
                plan_hash=plan['plan_hash'],targets=plan['targets'],stop_backends=stop,force=force,cancel_event=self.app.cancel_event)
        def finish(result):
            self.backup=result['backup']; self.plan=None; self.bundle=None
            self.show_verification(result['verification'])
            self.report('配置与登录缓存已写入；远端备份：'+self.backup)
            for warning in result.get('warnings',[]): self.report(warning)
            if result.get('restart_required'): self.report('发现后端仍在运行/自动重启；请断开并重新连接该 SSH 项目，避免继续用进程内旧状态。')
            if self.remote.host.get().strip()==self.host:
                self.remote.home.set(result['home']); self.remote.db.set(result['db'])
                self.remote.set_target(result['after']['model'],result['after']['provider'],'已同步的本机连接')
            self.status.set('已写入；验证：'+result['verification']['status']+'。请重连 SSH 项目测试新对话。')
        self.report('备份 → 核对后端 → 同步连接与认证 → 验证…'); self.queue(work,finish)

    def show_verification(self,value):
        self.report('验证结果：'+json.dumps(value,ensure_ascii=False))

    def action(self,action):
        if action in ('device_poll','device_cancel') and not self.login_id: return
        home=self.home.get().strip(); db=self.db.get().strip(); login=self.login_id
        def work(): return ssh_remote.request(self.host,action,home=home,db=db,login_id=login,cancel_event=self.app.cancel_event)
        def finish(result):
            if action=='connection_verify': self.show_verification(result['verification']); return
            self.login_id=result.get('login_id',''); status=result['status']
            if self.login_id and status in ('starting','awaiting-user'):
                self.app.auth_sessions[self.login_id]={'host':self.host,'home':result['home'],'db':result.get('db',''),'login_id':self.login_id}
            elif self.login_id: self.app.auth_sessions.pop(self.login_id,None)
            self.url=result.get('verification_url',''); code=result.get('user_code','')
            self.device.set('官方页面：'+self.url+'    一次性验证码：'+code if code else '授权状态：'+status)
            if status=='completed':
                self.login_id=''; self.device.set('授权已完成。请点击“验证远端账号 / 连接”，然后重新连接 SSH 项目。')
                self.report('远端设备码登录完成，等待验证。')
            elif status in ('ended','cancelled'):
                self.login_id=''; self.report('授权进程已结束或取消。可重新启动授权；无需上传任何验证码给我。')
            self.next_poll=time.monotonic()+3
        self.queue(work,finish)

    def restore(self):
        if not self.backup: self.report('本窗口尚无已完成的配置同步备份。'); return
        home=self.home.get().strip(); db=self.db.get().strip(); backup=self.backup
        def work(): return ssh_remote.request(self.host,'connection_restore',home=home,db=db,backup=backup,cancel_event=self.app.cancel_event)
        self.queue(work,lambda result:self.report('已恢复原配置和登录缓存；请重新连接 SSH 项目。'))

    def open_url(self):
        if self.url.startswith('https://auth.openai.com/'): webbrowser.open(self.url)

    def tick(self):
        if self.login_id and not self.app.future and time.monotonic()>=self.next_poll:
            self.next_poll=time.monotonic()+3; self.action('device_poll')
        if not self.app.closing: self.timer=self.root.after(500,self.tick)

    def close(self):
        if self.app.future:
            self.report('操作正在进行。主窗口 X 可安全退出；已发送的写入会等待结果。'); return
        if self.login_id:
            self.report('请先点“取消本次授权”，或在浏览器完成授权后再关闭此窗口。'); return
        self.bundle=None; self.plan=None; self.root.destroy()
