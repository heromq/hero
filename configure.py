#!/usr/bin/python3

import os, os.path, textwrap, argparse, sys, shlex, subprocess, tempfile, re, platform
from distutils.spawn import find_executable

tempfile.tempdir = "./build/tmp"

configure_args = str.join(' ', [shlex.quote(x) for x in sys.argv[1:]])

for line in open('/etc/os-release'):
    key, _, value = line.partition('=')
    value = value.strip().strip('"')
    if key == 'ID':
        os_ids = [value]
    if key == 'ID_LIKE':
        os_ids += value.split(' ')

# distribution "internationalization", converting package names.
# Fedora name is key, values is distro -> package name dict.
i18n_xlat = {
    'boost-devel': {
        'debian': 'libboost-dev',
        'ubuntu': 'libboost-dev (libboost1.55-dev on 14.04)',
    },
}

def pkgname(name):
    if name in i18n_xlat:
        dict = i18n_xlat[name]
        for id in os_ids:
            if id in dict:
                return dict[id]
    return name

def get_flags():
    with open('/proc/cpuinfo') as f:
        for line in f:
            if line.strip():
                if line.rstrip('\n').startswith('flags'):
                    return re.sub(r'^flags\s+: ', '', line).split()

def add_tristate(arg_parser, name, dest, help):
    arg_parser.add_argument('--enable-' + name, dest = dest, action = 'store_true', default = None,
                            help = 'Enable ' + help)
    arg_parser.add_argument('--disable-' + name, dest = dest, action = 'store_false', default = None,
                            help = 'Disable ' + help)

def apply_tristate(var, test, note, missing):
    if (var is None) or var:
        if test():
            return True
        elif var == True:
            print(missing)
            sys.exit(1)
        else:
            print(note)
            return False
    return False

def have_pkg(package):
    return subprocess.call(['pkg-config', package]) == 0

def pkg_config(option, package):
    output = subprocess.check_output(['pkg-config', option, package])
    return output.decode('utf-8').strip()

def try_compile(compiler, source = '', flags = []):
    return try_compile_and_link(compiler, source, flags = flags + ['-c'])

def ensure_tmp_dir_exists():
    if not os.path.exists(tempfile.tempdir):
        os.makedirs(tempfile.tempdir)

def try_compile_and_link(compiler, source = '', flags = []):
    ensure_tmp_dir_exists()
    with tempfile.NamedTemporaryFile() as sfile:
        ofile = tempfile.mktemp()
        try:
            sfile.file.write(bytes(source, 'utf-8'))
            sfile.file.flush()
            # We can't write to /dev/null, since in some cases (-ftest-coverage) gcc will create an auxiliary
            # output file based on the name of the output file, and "/dev/null.gcsa" is not a good name
            return subprocess.call([compiler, '-x', 'c++', '-o', ofile, sfile.name] + args.user_cflags.split() + flags,
                                   stdout = subprocess.DEVNULL,
                                   stderr = subprocess.DEVNULL) == 0
        finally:
            if os.path.exists(ofile):
                os.unlink(ofile)

def flag_supported(flag, compiler):
    # gcc ignores -Wno-x even if it is not supported
    adjusted = re.sub('^-Wno-', '-W', flag)
    split = adjusted.split(' ')
    return try_compile(flags = ['-Werror'] + split, compiler = compiler)

def debug_flag(compiler):
    src_with_auto = textwrap.dedent('''\
        template <typename T>
        struct x { auto f() {} };

        x<int> a;
        ''')
    if try_compile(source = src_with_auto, flags = ['-g', '-std=gnu++1y'], compiler = compiler):
        return '-g'
    else:
        print('Note: debug information disabled; upgrade your compiler')
        return ''

def gold_supported(compiler):
    src_main = 'int main(int argc, char **argv) { return 0; }'
    if try_compile_and_link(source = src_main, flags = ['-fuse-ld=gold'], compiler = compiler):
        return '-fuse-ld=gold'
    else:
        print('Note: gold not found; using default system linker')
        return ''

def maybe_static(flag, libs):
    if flag and not args.static:
        libs = '-Wl,-Bstatic {} -Wl,-Bdynamic'.format(libs)
    return libs

def default_target_arch():
    mach = platform.machine()
    if platform.machine() in ['i386', 'i686', 'x86_64']:
        return 'nehalem'
    else:
        return ''

modes = {
    'debug': {
        'sanitize': '-fsanitize=address -fsanitize=leak -fsanitize=undefined',
        'sanitize_libs': '-lasan -lubsan',
        'opt': '-O0 -DDEBUG -DDEBUG_SHARED_PTR -DDEFAULT_ALLOCATOR -DDEBUG_LSA_SANITIZER',
        'libs': '',
    },
    'release': {
        'sanitize': '',
        'sanitize_libs': '',
        'opt': '-O3',
        'libs': '',
    },
}

hero_tests = []

perf_tests = []

apps = ['hero',]

tests = hero_tests + perf_tests

other = []

all_artifacts = apps + tests + other

arg_parser = argparse.ArgumentParser('Configure HeroMQ')
arg_parser.add_argument('--static', dest = 'static', action = 'store_const', default = '',
                        const = '-static',
                        help = 'Static link (useful for running on hosts outside the build environment')
arg_parser.add_argument('--pie', dest = 'pie', action = 'store_true',
                        help = 'Build position-independent executable (PIE)')
arg_parser.add_argument('--so', dest = 'so', action = 'store_true',
                        help = 'Build shared object (SO) instead of executable')
arg_parser.add_argument('--mode', action='store', choices=list(modes.keys()) + ['all'], default='all')
arg_parser.add_argument('--with', dest='artifacts', action='append', choices=all_artifacts, default=[])
arg_parser.add_argument('--cflags', action = 'store', dest = 'user_cflags', default = '',
                        help = 'Extra flags for the C++ compiler')
arg_parser.add_argument('--ldflags', action = 'store', dest = 'user_ldflags', default = '',
                        help = 'Extra flags for the linker')
arg_parser.add_argument('--target', action = 'store', dest = 'target', default = default_target_arch(),
                        help = 'Target architecture (-march)')
arg_parser.add_argument('--compiler', action = 'store', dest = 'cxx', default = 'g++',
                        help = 'C++ compiler path')
arg_parser.add_argument('--c-compiler', action='store', dest='cc', default='gcc',
                        help='C compiler path')
arg_parser.add_argument('--with-osv', action = 'store', dest = 'with_osv', default = '',
                        help = 'Shortcut for compile for OSv')
arg_parser.add_argument('--enable-dpdk', action = 'store_true', dest = 'dpdk', default = False,
                        help = 'Enable dpdk (from seastar dpdk sources)')
arg_parser.add_argument('--dpdk-target', action = 'store', dest = 'dpdk_target', default = '',
                        help = 'Path to DPDK SDK target location (e.g. <DPDK SDK dir>/x86_64-native-linuxapp-gcc)')
arg_parser.add_argument('--debuginfo', action = 'store', dest = 'debuginfo', type = int, default = 1,
                        help = 'Enable(1)/disable(0)compiler debug information generation')
arg_parser.add_argument('--static-stdc++', dest = 'staticcxx', action = 'store_true',
			help = 'Link libgcc and libstdc++ statically')
arg_parser.add_argument('--static-boost', dest = 'staticboost', action = 'store_true',
            help = 'Link boost statically')
arg_parser.add_argument('--static-yaml-cpp', dest = 'staticyamlcpp', action = 'store_true',
            help = 'Link libyaml-cpp statically')
arg_parser.add_argument('--tests-debuginfo', action = 'store', dest = 'tests_debuginfo', type = int, default = 0,
                        help = 'Enable(1)/disable(0)compiler debug information generation for tests')
arg_parser.add_argument('--python', action = 'store', dest = 'python', default = 'python3',
                        help = 'Python3 path')
add_tristate(arg_parser, name = 'hwloc', dest = 'hwloc', help = 'hwloc support')
add_tristate(arg_parser, name = 'xen', dest = 'xen', help = 'Xen support')
arg_parser.add_argument('--enable-gcc6-concepts', dest='gcc6_concepts', action='store_true', default=False,
                        help='enable experimental support for C++ Concepts as implemented in GCC 6')
arg_parser.add_argument('--enable-alloc-failure-injector', dest='alloc_failure_injector', action='store_true', default=False,
                        help='enable allocation failure injection')
args = arg_parser.parse_args()

defines = []

extra_cxxflags = {}

hero_core = ([])

api = []

hero_tests_dependencies = hero_core + []

hero_tests_seastar_deps = []

deps = {
    'hero': ['main.cc',] + hero_core + api,
}

pure_boost_tests = set([])

tests_not_using_seastar_test_framework = set([]) | pure_boost_tests

for t in tests_not_using_seastar_test_framework:
    if not t in hero_tests:
        raise Exception("Test %s not found in hero_tests" % (t))

for t in hero_tests:
    deps[t] = [t + '.cc']
    if t not in tests_not_using_seastar_test_framework:
        deps[t] += hero_tests_dependencies
        deps[t] += hero_tests_seastar_deps
    else:
        deps[t] += hero_core

perf_tests_seastar_deps = []

for t in perf_tests:
    deps[t] = [t + '.cc'] + hero_tests_dependencies + perf_tests_seastar_deps

warnings = [
    '-Wno-mismatched-tags',  # clang-only
    '-Wno-maybe-uninitialized', # false positives on gcc 5
    '-Wno-tautological-compare',
    '-Wno-parentheses-equality',
    '-Wno-c++11-narrowing',
    '-Wno-c++1z-extensions',
    '-Wno-sometimes-uninitialized',
    '-Wno-return-stack-address',
    '-Wno-missing-braces',
    '-Wno-unused-lambda-capture',
    '-Wno-misleading-indentation',
    '-Wno-overflow',
    '-Wno-noexcept-type',
    '-Wno-nonnull-compare'
    ]

warnings = [w
            for w in warnings
            if flag_supported(flag = w, compiler = args.cxx)]

warnings = ' '.join(warnings + ['-Wno-error=deprecated-declarations'])

optimization_flags = [
    '--param inline-unit-growth=300',
]
optimization_flags = [o
                      for o in optimization_flags
                      if flag_supported(flag = o, compiler = args.cxx)]
modes['release']['opt'] += ' ' + ' '.join(optimization_flags)

gold_linker_flag = gold_supported(compiler = args.cxx)

dbgflag = debug_flag(args.cxx) if args.debuginfo else ''
tests_link_rule = 'link' if args.tests_debuginfo else 'link_stripped'

if args.so:
    args.pie = '-shared'
    args.fpie = '-fpic'
elif args.pie:
    args.pie = '-pie'
    args.fpie = '-fpie'
else:
    args.pie = ''
    args.fpie = ''

# a list element means a list of alternative packages to consider
# the first element becomes the HAVE_pkg define
# a string element is a package name with no alternatives
optional_packages = [['libsystemd', 'libsystemd-daemon']]
pkgs = []

def setup_first_pkg_of_list(pkglist):
    # The HAVE_pkg symbol is taken from the first alternative
    upkg = pkglist[0].upper().replace('-', '_')
    for pkg in pkglist:
        if have_pkg(pkg):
            pkgs.append(pkg)
            defines.append('HAVE_{}=1'.format(upkg))
            return True
    return False

for pkglist in optional_packages:
    if isinstance(pkglist, str):
        pkglist = [pkglist]
    if not setup_first_pkg_of_list(pkglist):
        if len(pkglist) == 1:
            print('Missing optional package {pkglist[0]}'.format(**locals()))
        else:
            alternatives = ':'.join(pkglist[1:])
            print('Missing optional package {pkglist[0]} (or alteratives {alternatives})'.format(**locals()))

if not try_compile(compiler=args.cxx, source='#include <boost/version.hpp>'):
    print('Boost not installed.  Please install {}.'.format(pkgname("boost-devel")))
    sys.exit(1)

if not try_compile(compiler=args.cxx, source='''\
        #include <boost/version.hpp>
        #if BOOST_VERSION < 105500
        #error Boost version too low
        #endif
        '''):
    print('Installed boost version too old.  Please update {}.'.format(pkgname("boost-devel")))
    sys.exit(1)


has_sanitize_address_use_after_scope = try_compile(compiler=args.cxx, flags=['-fsanitize-address-use-after-scope'], source='int f() {}')

defines = ' '.join(['-D' + d for d in defines])

globals().update(vars(args))

total_memory = os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES')
link_pool_depth = max(int(total_memory / 7e9), 1)

build_modes = modes if args.mode == 'all' else [args.mode]
build_artifacts = all_artifacts if not args.artifacts else args.artifacts

status = subprocess.call("./HERO-VERSION-GENERATOR")
if status != 0:
    print('Version file generation failed')
    sys.exit(1)

file = open('build/HERO-VERSION-FILE', 'r')
hero_version = file.read().strip()
file = open('build/HERO-RELEASE-FILE', 'r')
hero_release = file.read().strip()

extra_cxxflags["release.cc"] = "-DHERO_VERSION=\"\\\"" + hero_version + "\\\"\" -DHERO_RELEASE=\"\\\"" + hero_release + "\\\"\""

seastar_flags = []
if args.dpdk:
    # fake dependencies on dpdk, so that it is built before anything else
    seastar_flags += ['--enable-dpdk']
elif args.dpdk_target:
    seastar_flags += ['--dpdk-target', args.dpdk_target]
if args.staticcxx:
    seastar_flags += ['--static-stdc++']
if args.staticboost:
    seastar_flags += ['--static-boost']
if args.staticyamlcpp:
    seastar_flags += ['--static-yaml-cpp']
if args.gcc6_concepts:
    seastar_flags += ['--enable-gcc6-concepts']
if args.alloc_failure_injector:
    seastar_flags += ['--enable-alloc-failure-injector']

seastar_cflags = args.user_cflags
if args.target != '':
    seastar_cflags += ' -march=' + args.target
seastar_ldflags = args.user_ldflags
seastar_flags += ['--compiler', args.cxx, '--c-compiler', args.cc, '--cflags=%s' % (seastar_cflags), '--ldflags=%s' %(seastar_ldflags),
                  '--c++-dialect=gnu++1z', '--optflags=%s' % (modes['release']['opt']),
                 ]

status = subprocess.call([python, './configure.py'] + seastar_flags, cwd = 'seastar')

if status != 0:
    print('Seastar configuration failed')
    sys.exit(1)


pc = { mode : 'build/{}/seastar.pc'.format(mode) for mode in build_modes }
ninja = find_executable('ninja') or find_executable('ninja-build')
if not ninja:
    print('Ninja executable (ninja or ninja-build) not found on PATH\n')
    sys.exit(1)
status = subprocess.call([ninja] + list(pc.values()), cwd = 'seastar')
if status:
    print('Failed to generate {}\n'.format(pc))
    sys.exit(1)

for mode in build_modes:
    cfg =  dict([line.strip().split(': ', 1)
                 for line in open('seastar/' + pc[mode])
                 if ': ' in line])
    if args.staticcxx:
        cfg['Libs'] = cfg['Libs'].replace('-lstdc++ ', '')
    modes[mode]['seastar_cflags'] = cfg['Cflags']
    modes[mode]['seastar_libs'] = cfg['Libs']

seastar_deps = 'practically_anything_can_change_so_lets_run_it_every_time_and_restat.'

libs = ' '.join([maybe_static(args.staticyamlcpp, '-lyaml-cpp'), '-llz4', '-lz',
                 maybe_static(args.staticboost, '-lboost_filesystem'), ' -lcrypt', ' -lcryptopp',
                 maybe_static(args.staticboost, '-lboost_date_time'),
                ])

if not args.staticboost:
    args.user_cflags += ' -DBOOST_TEST_DYN_LINK'

for pkg in pkgs:
    args.user_cflags += ' ' + pkg_config('--cflags', pkg)
    libs += ' ' + pkg_config('--libs', pkg)
user_cflags = args.user_cflags
user_ldflags = args.user_ldflags
if args.staticcxx:
    user_ldflags += " -static-libgcc -static-libstdc++"

outdir = 'build'
buildfile = 'build.ninja'

os.makedirs(outdir, exist_ok = True)
do_sanitize = True
if args.static:
    do_sanitize = False

with open(buildfile, 'w') as f:
    f.write(textwrap.dedent('''\
        configure_args = {configure_args}
        builddir = {outdir}
        cxx = {cxx}
        cxxflags = {user_cflags} {warnings} {defines}
        ldflags = {gold_linker_flag} {user_ldflags}
        libs = {libs}
        pool link_pool
            depth = {link_pool_depth}
        pool seastar_pool
            depth = 1
        rule gen
            command = echo -e $text > $out
            description = GEN $out
        rule swagger
            command = seastar/json/json2code.py -f $in -o $out
            description = SWAGGER $out
        rule ninja
            command = {ninja} -C $subdir $target
            restat = 1
            description = NINJA $out
        rule copy
            command = cp $in $out
            description = COPY $out
        ''').format(**globals()))
    for mode in build_modes:
        modeval = modes[mode]
        f.write(textwrap.dedent('''\
            cxxflags_{mode} = {opt} -DXXH_PRIVATE_API -I. -I $builddir/{mode}/gen -I seastar -I seastar/build/{mode}/gen
            rule cxx.{mode}
              command = $cxx -MD -MT $out -MF $out.d {seastar_cflags} $cxxflags $cxxflags_{mode} $obj_cxxflags -c -o $out $in
              description = CXX $out
              depfile = $out.d
            rule link.{mode}
              command = $cxx  $cxxflags_{mode} {sanitize_libs} $ldflags {seastar_libs} -o $out $in $libs $libs_{mode}
              description = LINK $out
              pool = link_pool
            rule link_stripped.{mode}
              command = $cxx  $cxxflags_{mode} -s {sanitize_libs} $ldflags {seastar_libs} -o $out $in $libs $libs_{mode}
              description = LINK (stripped) $out
              pool = link_pool
            rule ar.{mode}
              command = rm -f $out; ar cr $out $in; ranlib $out
              description = AR $out
            ''').format(mode = mode, **modeval))
        f.write('build {mode}: phony {artifacts}\n'.format(mode = mode,
            artifacts = str.join(' ', ('$builddir/' + mode + '/' + x for x in build_artifacts))))
        compiles = {}
        for binary in build_artifacts:
            if binary in other:
                continue
            srcs = deps[binary]
            objs = ['$builddir/' + mode + '/' + src.replace('.cc', '.o')
                    for src in srcs
                    if src.endswith('.cc')]
            if binary.endswith('.a'):
                f.write('build $builddir/{}/{}: ar.{} {}\n'.format(mode, binary, mode, str.join(' ', objs)))
            else:
                if binary.startswith('tests/'):
                    local_libs = '$libs'
                    if binary not in tests_not_using_seastar_test_framework or binary in pure_boost_tests:
                        local_libs += ' ' + maybe_static(args.staticboost, '-lboost_unit_test_framework')
                    # Our code's debugging information is huge, and multiplied
                    # by many tests yields ridiculous amounts of disk space.
                    # So we strip the tests by default; The user can very
                    # quickly re-link the test unstripped by adding a "_g"
                    # to the test name, e.g., "ninja build/release/testname_g"
                    f.write('build $builddir/{}/{}: {}.{} {} {}\n'.format(mode, binary, tests_link_rule, mode, str.join(' ', objs),
                                                                                     'seastar/build/{}/libseastar.a'.format(mode)))
                    f.write('   libs = {}\n'.format(local_libs))
                    f.write('build $builddir/{}/{}_g: link.{} {} {}\n'.format(mode, binary, mode, str.join(' ', objs),
                                                                              'seastar/build/{}/libseastar.a'.format(mode)))
                    f.write('   libs = {}\n'.format(local_libs))
                else:
                    f.write('build $builddir/{}/{}: link.{} {} {}\n'.format(mode, binary, mode, str.join(' ', objs),
                                                                            'seastar/build/{}/libseastar.a'.format(mode)))
            for src in srcs:
                if src.endswith('.cc'):
                    obj = '$builddir/' + mode + '/' + src.replace('.cc', '.o')
                    compiles[obj] = src
                else:
                    raise Exception('No rule for ' + src)
        for obj in compiles:
            src = compiles[obj]
            gen_headers = list()
            gen_headers += ['seastar/build/{}/gen/http/request_parser.hh'.format(mode)]
            gen_headers += ['seastar/build/{}/gen/http/http_response_parser.hh'.format(mode)]
            f.write('build {}: cxx.{} {} || {} \n'.format(obj, mode, src, ' '.join(gen_headers)))
            if src in extra_cxxflags:
                f.write('    cxxflags = {seastar_cflags} $cxxflags $cxxflags_{mode} {extra_cxxflags}\n'.format(mode = mode, extra_cxxflags = extra_cxxflags[src], **modeval))
        f.write('  pool = seastar_pool\n')
        f.write('  subdir = seastar\n')
    f.write('build {}: phony\n'.format(seastar_deps))
    f.write(textwrap.dedent('''\
        rule configure
          command = {python} configure.py $configure_args
          generator = 1
        build build.ninja: configure | configure.py seastar/configure.py
        rule cscope
            command = find -name '*.[chS]' -o -name "*.cc" -o -name "*.hh" | cscope -bq -i-
            description = CSCOPE
        build cscope: cscope
        rule clean
            command = rm -rf build
            description = CLEAN
        build clean: clean
        default {modes_list}
        ''').format(modes_list = ' '.join(build_modes), **globals()))
