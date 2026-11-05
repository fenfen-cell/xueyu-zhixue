"""Optional reviewer login. Credentials are provisioned locally, never via a public API."""
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from http.cookies import SimpleCookie
from pathlib import Path


def password_record(password):
    if len(password) < 12:
        raise ValueError('密码至少 12 位')
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 600000).hex()
    return {'salt':salt, 'digest':digest, 'iterations':600000}


class Access:
    def __init__(self):
        self.lock = threading.Lock()
        self.sessions = {}
        self.failures = {}

    @property
    def enabled(self):
        return os.getenv('XY_AUTH_ENABLED','0') == '1'

    def users(self):
        path = Path(os.getenv('XY_USERS_FILE', 'runtime/users.json'))
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}

    def current(self, cookie):
        if not self.enabled:
            return 'local'
        try:
            parsed = SimpleCookie(cookie or '')
            token = parsed['xy_session'].value
        except (KeyError, ValueError):
            return None
        with self.lock:
            entry = self.sessions.get(token)
            if not entry or entry['expires'] < time.time():
                self.sessions.pop(token,None)
                return None
            return entry['user']

    def login(self, username, password, ip):
        if not self.enabled:
            raise ValueError('本地模式无需登录')
        if not isinstance(username,str) or not isinstance(password,str) or len(password)>256:
            raise ValueError('账号或密码错误')
        key = ip
        with self.lock:
            recent = [t for t in self.failures.get(key,[]) if time.time()-t < 600]
            if len(recent) >= 6:
                raise ValueError('登录尝试过多，请 10 分钟后再试')
            if len(self.failures)>2000:
                self.failures.clear()
            self.failures[key] = recent + [time.time()]
        record = self.users().get(username)
        # Perform the same expensive operation for nonexistent users.
        salt = record.get('salt') if record else '0'*32
        rounds = record.get('iterations',600000) if record else 600000
        digest = hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),rounds).hex()
        if not record or not hmac.compare_digest(digest,record['digest']):
            raise ValueError('账号或密码错误')
        token = secrets.token_urlsafe(32)
        with self.lock:
            self.failures.pop(key,None)
            self.sessions = {k:v for k,v in self.sessions.items() if v['expires']>time.time()}
            if len(self.sessions)>500:
                raise ValueError('登录会话已满，请稍后重试')
            self.sessions[token] = {'user':username,'expires':time.time()+8*3600}
        return token

    def logout(self, cookie):
        try:
            parsed=SimpleCookie(cookie or '')
            token=parsed['xy_session'].value
        except (KeyError,ValueError):
            return
        with self.lock:
            self.sessions.pop(token,None)

    def workspace(self, user):
        return hashlib.sha256(user.encode()).hexdigest()[:24]

    def role(self,user):
        if not self.enabled:return 'teacher'
        return self.users().get(user,{}).get('role','reviewer')


def create_account(path, username, password, role=None):
    if not re.fullmatch(r'[a-zA-Z0-9_-]{3,40}',username):
        raise ValueError('账号需为 3–40 位英文字母、数字、下划线或短横线')
    path=Path(path)
    records=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    selected=role or records.get(username,{}).get('role','reviewer')
    if selected not in {'reviewer','demo','teacher'}:raise ValueError('账号角色无效')
    records[username]={**password_record(password),'role':selected}
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(records,indent=2),encoding='utf-8')
    temporary.replace(path)


if __name__=='__main__':
    import argparse
    import getpass
    parser=argparse.ArgumentParser(description='创建或重设评审账号；密码不进入命令行与日志')
    parser.add_argument('username')
    parser.add_argument('--file',default='runtime/users.json')
    args=parser.parse_args()
    password=getpass.getpass('Password (12+ characters): ')
    if password != getpass.getpass('Repeat password: '):
        raise SystemExit('Passwords differ')
    create_account(args.file,args.username,password)
    print('Account saved. Restart the service to invalidate existing sessions after a password change.')
