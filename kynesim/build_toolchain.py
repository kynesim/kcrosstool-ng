#! /usr/bin/env python

"""
Main program. Download crosstool, configure it, and start
building toolchains.

Syntax:

build_toolchain.py [opts] <version>

<version> will be stored in the toolchain name.

The default working directory is /tmp/tc; change it with --work .

"""
import os
import getpass
import sys
import tempfile
import platform
import subprocess
import datetime
import re
import traceback


from optparse import OptionParser

g_verbose = False
if os.name == 'posix':
    g_is_linux = True
else: 
    g_is_linux = False

main_parser = OptionParser(usage = __doc__)
main_parser.add_option("-j", '--j', action="store",
                       dest="j",
                       default="4",
                       help="Number of concurrent threads to use when building")
main_parser.add_option("-v", "--verbose", action="store_true",
                       dest="verbose", default=False,
                       help="be more chatty")
main_parser.add_option("--toolchain", 
                       dest="toolchain", action="store",
                       default = None,
                       help = ("Only act on the given toolchain"))
main_parser.add_option("--list-toolchains",
                       dest="list_toolchains", action="store_true",
                       default = False,
                       help = ("List the toolchains that will be built"))
main_parser.add_option("--just-ctng",
                       dest="just_ctng", action="store_true",
                       default=False,
                       help= ("Just install CTNG and stop"))
#main_parser.add_option("--ctng", 
#                       dest="ctng", action="store",
#                       default = None,
#                       help = "Use an existing ct-ng repo")
main_parser.add_option("--work",
                       dest="work",
                       action="store",
                       default="/tmp/tc",
                       help="Directory to work in")
main_parser.add_option("--toolchain-only",
                       dest="toolchain_only",
                       action="store_true",
                       default=False,
                       help="Build toolchains only")

class GiveUp(Exception):
    retval = 1
    
    def __init__(self,message = None, retval = 1):
        self.message = message
        self.retval = retval

    def __str__(self): 
        if self.message is None: 
            return ''
        else:
            return self.message

def run_to_stdout(cmd, allowFailure = False, cwd = None, 
                  env = None):
    """
    Run to terminal stdout

    returns rv.
    """
    if (g_verbose):
        print "> %s"%(" ".join(cmd))
    try:
        subprocess.check_call(cmd, stderr = subprocess.STDOUT, 
                              cwd = cwd , env = env)
        return 0
    except subprocess.CalledProcessError as e:
        if allowFailure:
            return e.returncode
        else:
            raise GiveUp(str(e))

def run_for_output(cmd, allowFailure  = False, cwd = None,
                   env = None):
    """
    Run an capture o/p , returns (rc, output)
    """
    if (g_verbose):
        print "> %s"%(" ".join(cmd))
    try:
        out = subprocess.check_output(cmd, stderr = subprocess.STDOUT,
                                      cwd = cwd, env = env)
        return (0, out)
    except subprocess.CalledProcessError as e:
        if allowFailure:
            return e.returncode, e.output
        else:
            p = []
            p.append(str(e))
            et = e.output.splitlines()
            p.extend([' {}'.format(x) for x in errortext])
            raise GiveUp('\n'.join(p))

def go(args):
    global g_verbose
 
    (opts,args) = main_parser.parse_args()
    g_verbose = opts.verbose

    if (len(args) != 1):
        main_parser.print_help();
        sys.exit(1)

    tc_name = args[0]

    if (g_is_linux):
        pkg_required = [ "gperf", "libtool", "build-essential" ]
        for i in pkg_required:
            rc = subprocess.call(["dpkg", "-s", i ])
            if (rc != 0):
                raise GiveUp("Please install %s from '%s' . "%(i, " ".join(pkg_required)))
    
    here = os.path.abspath(os.path.realpath(__file__))
    tc_base_dir = os.path.split(here)[0]
    tc_config_dir = os.path.join(tc_base_dir, 'toolchains')
    print "Using configurations from %s"%tc_config_dir

# Find the abs pathname of opts.ctng
    #work = tempfile.mkdtemp(prefix="build_toolchain_")
    if (opts.work is not  None): 
                       work = "/tmp/tc"
    try:
        os.mkdir(work)
    except:
        pass

    ctng_source = os.path.join(tc_base_dir, '..')
    ctng_inst = os.path.join(work, "inst")

    # For development!
    print("Working in %s"%work)
    if (opts.just_ctng) or (not opts.toolchain_only):
        ctng_build_dir = os.path.join(work, "ctng-source")
        run_to_stdout(["rm", "-rf", ctng_build_dir], allowFailure = True)

        os.chdir(work)
        try:
            os.mkdir(ctng_build_dir)
        except:
            pass
        run_to_stdout([ "rsync", "-avz", "--exclude", "kynesim/*",
                        "--exclude", ".git/*", os.path.join(ctng_source, "."), 
                        os.path.join(ctng_build_dir, '.') ])
        os.chdir(ctng_build_dir)
        run_to_stdout(["./bootstrap"])
        run_to_stdout(["./configure", "--prefix", 
                       ctng_inst ])
        run_to_stdout(["make"])
        run_to_stdout(["make", "install" ])
        if (opts.just_ctng):
            print("CTNG built in %s\n"%ctng_inst)
            return 

    # Right oh.
    # Let's make some cache directories .. 
    local_tarballs_dir = os.path.join(tc_base_dir, "tarballs")
    prefix_dir = os.path.join(work, tc_name)

    host_name = platform.node()
    
    now = datetime.datetime.now()
    user_name = getpass.getuser()
    pkg_version = "%s_%s@%s_%s"%(tc_name, 
                                 user_name,
                                 host_name,
                                 now.strftime('%Y%m%d%H%M%S'))
    print "> pkg_version is set to '%s'"%pkg_version
    try:
        os.mkdir(local_tarballs_dir)
    except:
        pass
    try:
        os.mkdir(prefix_dir)
    except:
        pass


    # Now build our toolchains ..
    some_files = os.listdir(tc_config_dir)

    #  A hash of { name -> subprocess }
    builds_in_progress = { }

    # dir stuff.
    pfx_dir_re = re.compile(r'^(([^_]+_)+)(.*)$')
    

    for l in some_files:
        # Ignore anything that starts with '.'
        if l[0] == '.':
            continue
        # Ignore editor backups.
        if l[-1] == '~':
            continue
        if ((opts.toolchain is not None) and
            opts.toolchain != l):
            continue
        print " - Building %s"%l
        print " -- Removing old build .. "
        work_dir = os.path.join(work, l)
        try:
            shutil.rmtree(work_dir)
        except:
            pass

        try:
            os.mkdir(work_dir)
        except:
            pass

        # So, our prefix directory is made up of the real prefix dir, plus
        # the components starting _ in the name .. 
        m = pfx_dir_re.match(l)
        if (m is not None):
            things = m.group(1).replace("_", "/")
            if (things[-1] == '/'):
                things = things[:-1]
            my_prefix_dir = os.path.join(prefix_dir, things, m.group(3))
        else:
            my_prefix_dir = os.path.join(prefix_dir, l)

        replacer = re.compile(r'@@@([^@]+)@@@')
        var = re.compile(r'^([^=]*)=(.*)$')
        subst = { 'LOCAL_TARBALLS_DIR' : local_tarballs_dir ,
                  'WORK_DIR' : os.path.join(work_dir,'build') ,
                  'PREFIX_DIR' : my_prefix_dir ,
                  'PKGVERSION' :  pkg_version  }
        subst_vars = { 'CT_LOCAL_TARBALLS_DIR' : '"@@@LOCAL_TARBALLS_DIR@@@"',
                       'CT_WORK_DIR' : '"@@@WORK_DIR@@@"',
                       'CT_PREFIX_DIR' : '"@@@PREFIX_DIR@@@"',
                       'CT_TOOLCHAIN_PKGVERSION' : '"@@@PKGVERSION@@@"' }

        # Bit horrific - the only way to get ct-ng to build in a particular
        # dir is with the -C option.

        print "  Substitute config file .."
        with open(os.path.join(tc_config_dir, l), 'r') as infile:
            with open(os.path.join(work_dir, '.config'), 'w') as outfile:
                lines = infile.readlines()
                for l in lines:
                    m = var.match(l)
                    if (m is not None):
                        k = m.group(1)
                        if (k in subst_vars):
                            l = "%s=%s\n"%(k,subst_vars[k])

                    while True:
                        m = replacer.search(l)
                        if (m is not None):
                            # There was one.
                            to_repl = m.group(1)
                            if (to_repl in subst):
                                l = l[:m.start()] + subst[to_repl] + l[m.end():]
                            else:
                                raise GiveUp("Attempt to substitute unknown key '%s'"%to_repl)
                        else:
                            break
                    # Now we have l ..
                    outfile.write(l)
        # We now have a config file. Yay!
        print("Building toolchain with -j %s"%(opts.j))
        try:
            obj = subprocess.Popen([ os.path.join(ctng_inst, 'bin', 'ct-ng'), 
                                 '-C',
                                     work_dir,
                                     'build.%s'%opts.j ])
        except OSError,e:
            raise GiveUp("Cannot run ct-ng - attempt to use --toolchain-only when no ct-ng was ever built?")
        (out_data, err_data) = obj.communicate()
        if (obj.returncode != 0):
            raise GiveUp("ct-ng failed")



if __name__ == "__main__":
    try:
        go(sys.argv[1:])
        sys.exit(0)
    except GiveUp as e:
        print("")
        print("%s"%e)
        traceback.print_exc()
        sys.exit(e.retval)


# End file.

        

