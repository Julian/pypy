import re

def extract_s390x_cpu_ids(lines):
    """ NOT_RPYTHON """
    ids = []

    re_number = re.compile("processor (\d+):")
    re_version = re.compile("version = ([0-9A-Fa-f]+)")
    re_id = re.compile("identification = ([0-9A-Fa-f]+)")
    re_machine = re.compile("machine = (\d+)")
    for line in lines:
        number = -1
        version = None
        ident = None
        machine = 0

        match = re_number.match(line)
        if not match:
            continue
        number = int(match.group(1))

        match = re_version.search(line)
        if match:
            version = match.group(1)

        match = re_version.search(line)
        if match:
            version = match.group(1)

        match = re_id.search(line)
        if match:
            ident = match.group(1)

        match = re_machine.search(line)
        if match:
            machine = int(match.group(1))

        ids.append((number, version, ident, machine))

    return ids


def s390x_cpu_revision():
    """ NOT_RPYTHON """
    # linux kernel does the same classification
    # http://lists.llvm.org/pipermail/llvm-commits/Week-of-Mon-20131028/193311.html

    with open("/proc/cpuinfo", "rb") as fd:
        lines = fd.read().splitlines()
        cpu_ids = extract_s390x_cpu_ids(lines)
    machine = -1
    for number, version, id, m in cpu_ids:
        if machine != -1:
            assert machine == m
        machine = m

    if machine == 2097 or machine == 2098:
        return "z10"
    if machine == 2817 or machine == 2818:
        return "z196"
    if machine == 2827 or machine == 2828:
        return "zEC12"
    if machine == 2964:
        return "z13"

    # well all others are unsupported!
    return "unknown"

def update_cflags(cflags):
    """ NOT_RPYTHON """
    # force the right target arch for s390x
    for cflag in cflags:
        if cflag.startswith('-march='):
            break
    else:
        # the default cpu architecture that is supported
        # older versions are not supported
        revision = s390x_cpu_revision()
        assert revision != 'unknown'
        cflags += ('-march='+revision,)
    cflags += ('-m64','-mzarch')
    return cflags
