"""Bounded local HTTP transport for the Hermes Kanban dashboard API."""
import fcntl
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import stat
import time
import urllib.error
import urllib.parse
import urllib.request

from hermes_run_request import NoRedirect


TOKEN = re.compile(rb'window\.__HERMES_SESSION_TOKEN__="([A-Za-z0-9_-]{20,128})";')
TASK_ID = re.compile(r't_[0-9a-f]{8}\Z')
BOARD = re.compile(r'[a-z][a-z0-9_-]{0,63}\Z')
TASK_FIELDS = frozenset({'title','body','assignee','tenant','idempotency_key',
                         'model_override','provider_override','workspace_kind',
                         'workspace_path','max_runtime_seconds'})
REQUIRED = frozenset({'title','body','assignee','tenant','idempotency_key'})
DEFAULTS = {'model_override':None,'provider_override':None,'workspace_kind':'scratch',
            'workspace_path':None,'max_runtime_seconds':None}


def valid_task(task):
    return (isinstance(task,dict) and REQUIRED <= task.keys() and not set(task)-TASK_FIELDS
            and all(isinstance(task[key],str) and task[key] for key in REQUIRED)
            and task['title']==task['title'].strip()
            and all(key not in task or isinstance(task[key],str) and task[key]
                    for key in ('model_override','provider_override','workspace_kind','workspace_path'))
            and ('provider_override' not in task or 'model_override' in task)
            and ('workspace_kind' not in task or task['workspace_kind'] in ('scratch','dir'))
            and ('max_runtime_seconds' not in task or
                 type(task['max_runtime_seconds']) is int and task['max_runtime_seconds']>0))


def task_matches(card, submitted):
    expected={**DEFAULTS,**submitted}
    return (isinstance(card,dict) and isinstance(card.get('id'),str)
            and TASK_ID.fullmatch(card['id']) is not None
            and all(type(card.get(key)) is type(value) and card.get(key)==value
                    for key,value in expected.items() if key in TASK_FIELDS))


class NoRequestSent(ValueError):
    """The client failed before task creation reached Hermes."""


class AmbiguousCreate(ValueError):
    """A task create may have reached Hermes; reconcile before another POST."""


class Client:
    def __init__(self, base_url='http://host.docker.internal:9119'):
        parsed=urllib.parse.urlsplit(base_url)
        if (parsed.scheme!='http' or parsed.hostname not in {'host.docker.internal','127.0.0.1'}
                or not parsed.port or parsed.hostname=='host.docker.internal' and parsed.port!=9119
                or parsed.path not in ('','/') or parsed.username or parsed.password
                or parsed.query or parsed.fragment):
            raise ValueError('Hermes dashboard must be local')
        self.base_url=base_url.rstrip('/')
        self.host=f'127.0.0.1:{parsed.port}'
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
        self.token=None

    def _request(self, path, *, data=None, token=None):
        headers={'Host':self.host}
        if token is not None:headers['X-Hermes-Session-Token']=token
        if data is not None:headers['Content-Type']='application/json'
        request=urllib.request.Request(self.base_url+path,data=data,headers=headers,
                                       method='POST' if data is not None else 'GET')
        with self.opener.open(request,timeout=10) as response:
            limit=1048576 if data is None else 65536
            body=response.read(limit+1)
            if response.status!=200 or len(body)>limit:
                raise ValueError('unexpected or oversized Hermes response')
            return body

    def _session_token(self):
        try:html=self._request('/')
        except urllib.error.HTTPError as error:
            error.close();raise NoRequestSent('Hermes dashboard is unavailable') from None
        except (urllib.error.URLError,TimeoutError,ValueError,http.client.HTTPException,OSError) as error:
            raise NoRequestSent('Hermes dashboard is unavailable') from error
        matches=TOKEN.findall(html)
        if len(matches)!=1:raise NoRequestSent('Hermes dashboard token is unavailable')
        self.token=matches[0].decode()
        return self.token

    def find_tasks(self, board, tenant, key):
        """Read active and archived cards; absence does not prove a POST never landed."""
        if (not isinstance(board,str) or not BOARD.fullmatch(board) or not isinstance(tenant,str)
                or not tenant or not isinstance(key,str) or not key):
            raise ValueError('invalid Kanban lookup')
        path='/api/plugins/kanban/board?'+urllib.parse.urlencode(
            {'board':board,'tenant':tenant,'include_archived':'true'})
        for attempt in range(2):
            token=self.token or self._session_token()
            try:raw=self._request(path,token=token)
            except urllib.error.HTTPError as error:
                if error.code==401 and attempt==0:
                    error.close();self.token=None;continue
                error.close();raise ValueError('Kanban readback unavailable') from None
            except (urllib.error.URLError,TimeoutError,ValueError,http.client.HTTPException,OSError) as error:
                raise ValueError('Kanban readback unavailable') from error
            try:data=json.loads(raw)
            except (UnicodeError,ValueError) as error:raise ValueError('invalid Kanban readback') from error
            columns=data.get('columns') if isinstance(data,dict) else None
            if not isinstance(columns,list):raise ValueError('invalid Kanban readback')
            matches=[]
            for column in columns:
                if not isinstance(column,dict) or not isinstance(column.get('tasks'),list):
                    raise ValueError('invalid Kanban readback')
                matches.extend(card for card in column['tasks'] if isinstance(card,dict)
                               and card.get('idempotency_key')==key and card.get('tenant')==tenant)
            if len(matches)>1:raise AmbiguousCreate('multiple Kanban cards share an operation key')
            return matches

    def create_task(self, board, task):
        if not isinstance(board,str) or not BOARD.fullmatch(board) or not valid_task(task):
            raise NoRequestSent('invalid Kanban task request')
        try:raw=json.dumps(task,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
        except (TypeError,ValueError) as error:raise NoRequestSent('invalid Kanban task JSON') from error
        if len(raw)>65536:raise NoRequestSent('Kanban task request too large')
        for attempt in range(2):
            token=self.token or self._session_token()
            try:
                body=self._request('/api/plugins/kanban/tasks?board='+board,data=raw,token=token)
            except urllib.error.HTTPError as error:
                if error.code==401 and attempt==0:
                    error.close();self.token=None;continue
                error.close()
                if error.code==401:raise NoRequestSent('Hermes rejected both session tokens') from None
                raise AmbiguousCreate('Kanban create response unavailable; reconcile before retry') from None
            except (urllib.error.URLError,TimeoutError,ValueError,http.client.HTTPException,OSError) as error:
                raise AmbiguousCreate('Kanban create outcome uncertain; reconcile before retry') from error
            try:value=json.loads(body)
            except (UnicodeError,ValueError) as error:
                raise AmbiguousCreate('Kanban create response invalid; reconcile before retry') from error
            card=value.get('task') if isinstance(value,dict) else None
            if not task_matches(card,task):
                raise AmbiguousCreate('Kanban create binding differs; reconcile before retry')
            return card


class RecentRequests:
    """Per-kind, persistent 72-hour submission throttle; not an effect receipt."""
    TTL=72*60*60
    KEY=re.compile(r'[a-z][a-z0-9_-]{1,127}\Z')

    def __init__(self, path):
        self.path=Path(path)
        self._safe_parent()
        self.lock=self.path.with_name(self.path.name+'.lock')

    def _safe_parent(self):
        if not self.path.is_absolute() or self.path.parent.is_symlink():
            raise ValueError('recent-request volume must be an owner-only directory')
        info=self.path.parent.stat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid()
                or stat.S_IMODE(info.st_mode)!=0o700):
            raise ValueError('recent-request volume must be an owner-only directory')

    def _load(self):
        try:fd=os.open(self.path,os.O_RDONLY|os.O_NOFOLLOW)
        except FileNotFoundError:return {}
        with os.fdopen(fd,'rb') as source:
            info=os.fstat(source.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid()
                    or stat.S_IMODE(info.st_mode)!=0o600 or info.st_nlink!=1 or info.st_size>1048576):
                raise ValueError('invalid recent-request cache file')
            try:value=json.loads(source.read(1048577))
            except (UnicodeError,ValueError) as error:raise ValueError('invalid recent-request cache JSON') from error
        if (not isinstance(value,dict) or any(not isinstance(key,str) or not self.KEY.fullmatch(key)
                or not isinstance(entry,dict) or set(entry)!={'at','uncertain'}
                or type(entry['at']) is not int or entry['at']<0 or type(entry['uncertain']) is not bool
                for key,entry in value.items())):
            raise ValueError('invalid recent-request cache schema')
        return value

    def _save(self, value):
        self._safe_parent()
        encoded=json.dumps(value,sort_keys=True,separators=(',',':')).encode()
        if len(encoded)>1048576:raise ValueError('recent-request cache full; reconcile before writing')
        temp=self.path.with_name('.'+self.path.name+'.tmp-'+secrets.token_hex(8))
        fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as out:out.write(encoded);out.flush();os.fsync(out.fileno())
        os.replace(temp,self.path)
        directory=os.open(self.path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:os.fsync(directory)
        finally:os.close(directory)

    def submit(self, key, send, *, now=None):
        self._safe_parent()
        if not isinstance(key,str) or not self.KEY.fullmatch(key):
            raise ValueError('invalid operation key')
        now=int(time.time()) if now is None else now
        if type(now) is not int or now<0:raise ValueError('invalid request timestamp')
        fd=os.open(self.lock,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'rb+') as lock:
            info=os.fstat(lock.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid()
                    or stat.S_IMODE(info.st_mode)!=0o600 or info.st_nlink!=1):
                raise ValueError('unsafe recent-request lock')
            fcntl.flock(lock,fcntl.LOCK_EX)
            entries=self._load();prior=entries.get(key)
            if prior:
                if prior['uncertain']:raise ValueError('request outcome requires reconciliation')
                if now<prior['at']:raise ValueError('request clock moved backwards')
                if now-prior['at']<self.TTL:return None
            entries[key]={'at':now,'uncertain':True}
            self._save(entries)
            try:result=send()
            except NoRequestSent:
                if prior is None:entries.pop(key)
                else:entries[key]=prior
                self._save(entries)
                raise
            entries[key]={'at':now,'uncertain':False}
            self._save(entries)
            return result

    def confirm(self, key, card, submitted):
        """Clear uncertainty only after an exact task binding is read back."""
        self._safe_parent()
        if (not isinstance(key,str) or not self.KEY.fullmatch(key) or not isinstance(submitted,dict)
                or not valid_task(submitted) or submitted['idempotency_key']!=key
                or not task_matches(card,submitted)):
            raise ValueError('invalid Kanban readback proof')
        fd=os.open(self.lock,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'rb+') as lock:
            info=os.fstat(lock.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid()
                    or stat.S_IMODE(info.st_mode)!=0o600 or info.st_nlink!=1):
                raise ValueError('unsafe recent-request lock')
            fcntl.flock(lock,fcntl.LOCK_EX)
            entries=self._load()
            if key not in entries or not entries[key]['uncertain']:
                raise ValueError('no uncertain request to reconcile')
            entries[key]['uncertain']=False
            self._save(entries)
