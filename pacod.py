#!/usr/bin/env python3
# Pacman optional dependency manager

import pyalpm
from utils.nolog import *

DB_COLORS = {
	'core': 1,
	'extra': 2,
	'local': 3,
}

class Pick:
	available = '[ ]'
	installed = '\033[94m[=]\033[39m'
	install = '\033[7;92m[+]\033[39m'
	upgrade = '\033[7;95m[^]\033[39m'
	reinstall = '\033[7;93m[@]\033[39m'

class TermInfo(list):
	iflag: int
	oflag: int
	cflag: int
	lflag: int
	ispeed: int
	ospeed: int
	cc: list[int]

	@property
	def iflag(self) -> int:
		return self[tty.IFLAG]

	@iflag.setter
	def iflag(self, iflag: int):
		self[tty.IFLAG] = iflag

	@property
	def oflag(self) -> int:
		return self[tty.OFLAG]

	@oflag.setter
	def oflag(self, oflag: int):
		self[tty.OFLAG] = oflag

	@property
	def cflag(self) -> int:
		return self[tty.CFLAG]

	@cflag.setter
	def cflag(self, cflag: int):
		self[tty.CFLAG] = cflag

	@property
	def lflag(self) -> int:
		return self[tty.LFLAG]

	@lflag.setter
	def lflag(self, lflag: int):
		self[tty.LFLAG] = lflag

	@property
	def ispeed(self) -> int:
		return self[tty.ISPEED]

	@ispeed.setter
	def ispeed(self, ispeed: int):
		self[tty.ISPEED] = ispeed

	@property
	def ospeed(self) -> int:
		return self[tty.OSPEED]

	@ospeed.setter
	def ospeed(self, ospeed: int):
		self[tty.OSPEED] = ospeed

	@property
	def cc(self) -> list:
		return self[tty.CC].copy()

	@cc.setter
	def cc(self, cc: list):
		self[tty.cc] = cc.copy()

	def set(self, fd=sys.stdin, when=tty.TCSANOW):
		tty.tcsetattr(fd, when, self)

	@classmethod
	def get(cls, fd=sys.stdin):
		return cls(tty.tcgetattr(fd))

class FSM:
	state: str

	def __init__(self, state):
		self.state = state

	def __call__(self, *args, **kwargs):
		self.state, *res = getattr(self, self.state)(*args, **kwargs)
		return res

class Key(str):
	def __repr__(self):
		return f"{self.__class__.__name__}({super().__repr__()})"

	def __str__(self):
		return self.__repr__()

class ControlKey(Key):
	C = '\3'

class ArrowKey(Key):
	UP     = 'A'
	DOWN   = 'B'
	RIGHT  = 'C'
	LEFT   = 'D'
	END    = 'F'
	HOME   = 'H'
	PGUP   = '5'
	PGDN   = '6'

class InputSM(FSM):
	_page: ArrowKey | None

	def __init__(self):
		super().__init__('default')

	def default(self, c):
		match c:
			case ControlKey.C: return 'default', ControlKey(c)
			case '\033': return 'escape',
			case _: return 'default', Key(c)

	def escape(self, c):
		match c:
			case '[': return 'bracket',
			case _: return 'default', '\033'+c

	def bracket(self, c):
		match c:
			case ArrowKey.UP | ArrowKey.DOWN | ArrowKey.RIGHT | ArrowKey.LEFT | ArrowKey.HOME | ArrowKey.END: return 'default', ArrowKey(c)
			case ArrowKey.PGUP | ArrowKey.PGDN: self._page = ArrowKey(c); return 'page',
			case _: return 'default', '\033['+c

	def page(self, c):
		match c:
			case '~': page, self._page = self._page, None; return 'default', page
			case _: self._page = None; return 'default', f"\033[{c}~"

@apmain
@aparg('package', nargs='+*'['--stdin' in sys.argv])
@aparg('--stdin', action='store_true')
def main(cargs):
	handle = pyalpm.Handle('/', '/var/lib/pacman/')
	localdb = handle.get_localdb()
	for i in os.listdir(os.path.join(handle.dbpath, 'sync/')):
		match os.path.splitext(i):
			case (name, '.db'):
				handle.register_syncdb(name, pyalpm.SIG_DATABASE_OPTIONAL)
	syncdbs = handle.get_syncdbs()

	optdeps = Sdict(dict)

	packages = list(cargs.package)
	if (cargs.stdin): packages += map(str.strip, sys.stdin)

	if (not sys.stdin.isatty()): sys.stdin = open('/dev/tty', 'r')

	for i in packages:
		pkg = localdb.get_pkg(i)
		if (pkg is not None):
			for j in pkg.optdepends:
				dep = re.match(r'^([\w.-]+)', j)[1]
				try: optdeps[pkg][j] = first(deps for db in syncdbs if (deps := tuple((dep, localdb.get_pkg(dep.name)) for dep in db.search(rf"^{dep}$"))))
				except StopIteration: logwarn(f"{i}: unknown dependency — {dep}")

	if (not optdeps): return

	termsize = None
	def resize(*_):
		nonlocal termsize
		termsize = os.terminal_size(tty.tcgetwinsize(sys.stdin)[::-1])
	signal.signal(signal.SIGWINCH, resize); resize()

	selected = int()
	selected_dep = None
	picked = set()

	oldterm = TermInfo.get(sys.stdin)
	try:
		tty.setraw(sys.stdin, tty.TCSAFLUSH)
		print('\033[?25l\033[s', end='', file=sys.stderr)

		inp = InputSM()

		skip = int()
		while (True):
			ii = ii_s = ii_p = ln = ln_p = int()
			for pkg, deps in optdeps.items():
				ln += 1
				if (ln_p+1 >= termsize.lines-1): break
				if (ln >= skip): print(f"\033[1;44m{pkg.name.center(min(termsize.columns, 132))}\033[22;49m", end='\r\n', file=sys.stderr); ln_p += 1

				ln += 1
				if (ln_p+1 >= termsize.lines-1): break
				if (ln >= skip): print(f"\033[40m{' '*min(termsize.columns, 132)}\033[49m", end='\r\n', file=sys.stderr); ln_p += 1

				for optdep, pkgs in deps.items():
					for kk, (dep, local_dep) in enumerate(pkgs):
						constr = optdep.partition(':')[0].replace(dep.name, '')
						upgrade = ''

						if (local_dep is None): pick = (Pick.available, Pick.install)[dep in picked]
						elif (pyalpm.vercmp(dep.version, local_dep.version) > 0): pick, upgrade = (Pick.installed, Pick.upgrade)[dep in picked], f" \033[22;92m→  {dep.version}\033[2;39m"
						else: pick = (Pick.installed, Pick.reinstall)[dep in picked]

						s = S(f"""\033[40m{' >'[ii == selected]} {pick}  \033[1;9{DB_COLORS.get(dep.db.name, 5)}m{dep.db.name}/\033[39m{dep.name}{f"\033[2m{terminal_link('provides '+S(', ').join(dep.provides, last='and'), 'as').join('  ')*(dep.name not in constr)}{constr}" if (constr) else ''}\033[22;39m  \033[2m[{(local_dep or dep).version}{upgrade}]\033[22m  """)
						desc = S(optdep.partition(':')[2].strip())
						end = S('\033[27m  \033[49m')

						if (ii == selected): selected_dep = dep
						ii += 1

						ln += 1
						if (ln_p+1 >= termsize.lines-1): break
						l = (min(termsize.columns, 132) - len(s.noesc()) - len(end.noesc()))
						if (ln >= skip): print((s + (((desc_ := desc.fit(l-2)) and (desc__ := S(re.sub(r'(`.*?(?:`|$))', '\033[96m\\1\033[39m', desc_))) and (desc__ if (desc_ == desc) else desc.hyperlink(desc__)).join('()'))).just(l).join(('\033[3m', '\033[23m')) + end), end='\r\n', file=sys.stderr); ln_p += 1; ii_s, ii_p = (ii_s or ii), ii

				ln += 1
				if (ln_p+1 >= termsize.lines-1): break
				if (ln >= skip): print(f"\033[40m{' '*min(termsize.columns, 132)}\033[49m", end='\r\n', file=sys.stderr); ln_p += 1
			print('\033[J', end='', file=sys.stderr, flush=True)

			if (selected is None): break

			for key in inp(sys.stdin.read(1)):
				match key:
					case ControlKey(ControlKey.C): raise KeyboardInterrupt(key)
					case ArrowKey(ArrowKey.UP):
						if (selected > ii_s-1): selected -= 1
						else: skip -= 1
					case ArrowKey(ArrowKey.DOWN):
						if (selected < ii_p-1): selected += 1
						else: skip += 1
					case ArrowKey(ArrowKey.HOME):
						if (skip and selected > ii_s-1): selected = ii_s-1
						else: selected = skip = 0
					case ArrowKey(ArrowKey.END):
						if (selected < ii_p-1): selected = ii_p-1
						else: selected, skip = ii, termsize.lines  # max
					case ArrowKey(ArrowKey.PGUP):
						selected = ii_s-1
					case ArrowKey(ArrowKey.PGDN):
						selected = ii_p-1
					case Key(' '): picked ^= {selected_dep}
					case Key('\n') | Key('\r'): selected = None
			selected = (max(ii_s-1, min(selected, ii_p-1)) if (selected is not None) else None)
			skip = (max(1, min(skip, ln-termsize.lines+3)) if (ln_p < ln) else 0)

			print(f"\033[{ln_p}F", end='', file=sys.stderr, flush=True)
			#print('\033[u', end='', file=sys.stderr)
	finally:
		print('\033[u\033[J\033[?25h', end='', file=sys.stderr)
		oldterm.set(sys.stdin, when=tty.TCSAFLUSH)

	if (picked):
		cmd = ('pacman', '-S', '--asdeps', *(f'{dep.db.name}/{dep.name}' for dep in picked))

		if (not os.path.exists(os.path.join(handle.dbpath, 'db.lck'))):
			if (os.getuid() != 0): cmd = ('sudo', *cmd)
			os.execvp(cmd[0], cmd)
		else:
			print('\033[1;96m$\033[22m', *cmd, end='\033[m\n', file=sys.stderr)

if (__name__ == '__main__'):
	try: exit(main())
	except KeyboardInterrupt as ex: exit(ex)

# by Sdore, 2023-25
#   www.sdore.me
