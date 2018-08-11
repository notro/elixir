import distutils.version
import os
import pathlib
import re
import subprocess
import sys
import tempfile


def sh(*args, **kwargs):
    # subprocess.run was introduced in Python 3.5
    # fall back to subprocess.check_output if it's not available
    if hasattr(subprocess, 'run'):
        p = subprocess.run(args, stdout=subprocess.PIPE, **kwargs)
        p = p.stdout
    else:
        p = subprocess.check_output(args, **kwargs)
    return p

def script(cmd, *args):
    res = None

    if cmd == 'update':
        res = update(*args)
    elif cmd == 'list-tags':
        if len(args) and args[0] == '-h':
            res = list_tags_h()
        else:
            res = list_tags()
    elif cmd == 'get-latest':
        res = get_latest()
    elif cmd == 'get-type':
        res = get_type(args[0], args[1])
    elif cmd == 'get-blob':
        res = get_blob(args[0])
    elif cmd == 'get-file':
        res = get_file(args[0], args[1])
    elif cmd == 'get-dir':
        res = get_dir(args[0], args[1])
    elif cmd == 'list-blobs':
        res = list_blobs(args[0], args[1])
    elif cmd == 'tokenize-file':
        res = tokenize_file(args[0], args[1])
#    elif cmd == 'untokenize':
#        res = untokenize(args[0], args[1])
    elif cmd == 'parse-defs':
        res = parse_defs(args[0], args[1])

    if res is not None:
        return res
    else:
        args = ('./script.sh',) + (cmd,) + args
        return sh(*args)


class Repo:
    def __init__(self, path, tag, rel=None, parent=None):
        self.path = pathlib.Path(path)
        if rel is None:
            rel = pathlib.Path('/')
        self.rel = rel
        self._submodules = None
        self._tag = tag
        self.parent = parent

    # https://stackoverflow.com/questions/26018979/get-bare-repository-submodule-hash
    @property
    def tag(self):
        if self._tag is None:
            path = str(self.rel)[1:]  # Strip off leading slash
            ls = self.parent.git('ls-tree', '-d', self.parent.tag, '--', path)
            try:
                commit = ls.split()[2].decode()
            except IndexError:
                commit = ''
            self._tag = commit
        return self._tag

    @property
    def tree(self):
        return Tree(self, self.tag)

    @property
    def submodules(self):
        if self._submodules is None:
            self._submodules = []
            subs_dir = self.path.parent / 'submodules'
            if not subs_dir.exists():
                return []
            for p in subs_dir.glob('**/refs'):

                rel = (pathlib.Path('/') / p.relative_to(subs_dir)).parent
                repo = Repo(p.parent, tag=None, rel=rel, parent=self)
                self._submodules.append(repo)
        return self._submodules

    def submodule(self, path):
        for sub in self.submodules:
            if str(path).startswith(str(sub.rel)):
                return sub
        return None

    def git(self, *args, **kwargs):
        args = ('git',) + args
        #print(repr(args), str(self.path))
        kwargs.setdefault('stderr', subprocess.DEVNULL)
        output = sh(*args, cwd=str(self.path), **kwargs)
        return output

    def ls_tree(self, path):
        p = str(path)
        if p.startswith('/'):
            p = p[1:]
        return self.git('ls-tree', '-l', '%s:%s' % (self.tag, p))

    def cat_file(self, arg, path, pathspec):
        version = self.tag
        p = str(path)[1:]  # Strip off leading slash
        args = ['cat-file', arg]
        if pathspec:
            args.append('%s:%s' % (version, p))
        else:
            args.extend([version, p])

        res = self.git(*args)
        if res:
            return res

        sub = self.submodule(path)
        if not sub:
            return b''
        rel = pathlib.Path('/') / path.relative_to(sub.rel)
        return sub.cat_file(arg, rel, pathspec)

    def cat_file_blob(self, path, pathspec=True):
        return self.cat_file('blob', path, pathspec)

    def __repr__(self):
        return 'Repo(path=%s, rel=%s, tag=%s)' % (self.path, self.rel, self.tag)


def get_versions():
    repo = Repo(os.environ['LXR_REPO_DIR'], None)
    all_tags = repo.git('tag').decode().splitlines()
    all_versions = [distutils.version.LooseVersion(tag) for tag in all_tags if tag[0].isdigit()]

    versions = []
    therest = []

    for ver in all_versions:
        if len(ver.version) == 3:
            versions.append(ver)
        else:
            therest.append(ver)

    # Add pre-releases when there's no release yet
    for ver in therest:
        if len(ver.version) > 3:
            pre = distutils.version.LooseVersion('%s.%s.%s' % (ver.version[0], ver.version[1], ver.version[2]))
            if pre not in versions:
                versions.append(ver)

    return sorted(versions, reverse=True)


def list_tags():
    versions = get_versions()
    out = ''.join(['%s\n' % (ver,) for ver in versions])
    return out.encode()


def list_tags_h():
    versions = get_versions()
    out = ''.join(['v%d %d.%d %s\n' % (ver.version[0], ver.version[0], ver.version[1], ver) for ver in versions])
    return out.encode()


def get_latest():
    versions = get_versions()
    out = '%s\n' % (versions[0],)
    return out.encode()


def get_type(version, path):
    repo = Repo(os.environ['LXR_REPO_DIR'], version)
    path = pathlib.Path('/') / path
    return repo.cat_file('-t', path, pathspec=True)


def get_blob(sha):
    repo = Repo(os.environ['LXR_REPO_DIR'], None)
    repos = [repo] + repo.submodules
    for r in repos:
        res = r.git('cat-file', 'blob', sha)
        if res:
            return res
    return b''


def get_file(version, path):
    repo = Repo(os.environ['LXR_REPO_DIR'], version)
    path = pathlib.Path('/') / path
    return repo.cat_file_blob(path, pathspec=True)


def get_dir(version, path):
    repo = Repo(os.environ['LXR_REPO_DIR'], version)
    path = pathlib.Path('/') / path

    output = repo.ls_tree(path)
    if not output:
        sub = repo.submodule(path)
        if sub:
            rel = pathlib.Path('/') / path.relative_to(sub.rel)
            output = sub.ls_tree(rel)

    entries = []

    for line in output.splitlines():
        start, _, name = line.partition(b'\t')
        if name.startswith(b'.'):
            continue
        mode, typ, sha, size = start.split()

        if typ == b'commit':
            sub = repo.submodule(path / name.decode())
            if sub:
                typ = b'tree'

        entries.append((typ, name, size))

    if not entries:
        return b''

    entries.sort(key=lambda x: x[1].lower())
    entries.sort(key=lambda x: x[0], reverse=True)

    lines = [b' '.join(x) for x in entries]
    return b'\n'.join(lines) + b'\n'


def tokenize_file(version, path):

    if version == '-b':
        rev = path
        repo = Repo(os.environ['LXR_REPO_DIR'], None)
        repos = [repo] + repo.submodules
        for r in repos:
            blob = r.git('cat-file', 'blob', rev)
            if blob:
                break
    else:
        repo = Repo(os.environ['LXR_REPO_DIR'], version)
        path = pathlib.Path('/') / path
        blob = repo.cat_file_blob(path, pathspec=True)

    if not blob:
        return b''


    if 1:  # Use perl for now
        prep = blob.replace(b'\n', b'\1')
        r = r's%((/\*.*?\*/|//.*?\001|"(\\.|.)*?"|# *include *<.*?>|\W)+)(\w+)?%\1\n\4\n%g'
        args = ('perl', '-pe', r)
        p = subprocess.run(args, stdout=subprocess.PIPE, input=prep)
        out = p.stdout

        if not out:
            return b''

        out = out[:out.rfind(b'\n')]

        return out

    # This can hang
    # Possibly: https://stackoverflow.com/questions/15820752/regex-re-findall-hangs-what-if-you-cant-read-line-by-line

    prep = blob.decode().replace('\n', '\1')

    r = r'((/\*.*?\*/|//.*?\001|"(\\.|.)*?"|# *include *<.*?>|\W)+)(\w+)?'

    res = re.findall(r, prep)
    out = ''.join(['%s\n%s\n' % (x[0], x[3]) for x in res])

    if not out:
        return b''

    out = out[:out.rfind('\n')]

    return out.encode()


def list_blobs(arg, tag):

    repo = Repo(os.environ['LXR_REPO_DIR'], tag)
    repos = [repo] + repo.submodules

    entries = []
    for r in repos:
        ls = r.git('ls-tree', '-r', r.tag)

        for line in ls.decode().splitlines():
            start, _, path = line.partition('\t')
            mode, typ, sha = start.split()
            path = str(r.rel / path)[1:]
            entries.append([mode, typ, sha, path])

    blobs = [x for x in entries if x[1] == 'blob']

    if arg == '-p':
        out = ''.join(['%s %s\n' % (x[2], x[3]) for x in blobs])
    elif arg == '-f':
        out = ''.join(['%s %s\n' % (x[2], os.path.basename(x[3])) for x in blobs])
    else:
        out = ''

    return out.encode()


def parse_defs(sha, filename):

    blob = get_blob(sha)
    if not blob:
        return b''

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, filename)
        with open(path, 'wb') as f:
            f.write(blob)
        ctags = sh('ctags', '-x', '--c-kinds=+p-m', path)

    ctags = [x.split() for x in ctags.splitlines() if not x.startswith(b'operator ')]

    out = b''.join([b' '.join(x[:3]) + b'\n' for x in ctags])

    return out


def update(*args):
    repo = Repo(os.environ['LXR_REPO_DIR'], None)
    repos = [repo] + repo.submodules
    #print('repos', repos)

    cmd = args[0] if len(args) else None

    for r in repos:
        print('%s\n' % (r.path,))
        if cmd == 'fetch':
            out = r.git('fetch', '-f', '--prune', '--progress', stderr=subprocess.STDOUT)
            sys.stdout.buffer.write(out)
            sys.stdout.flush()

    return b''
