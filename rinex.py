# Created by Nick Matteo <kundor@kundor.org> June 9, 2009
"""Utilities to read RINEX GPS file values.

Mostly you will use readfile.read_file(URL), where URL can be an http, ftp, or
local file path to a gzipped, compact, or standard RINEX observation file.
A GPSData object is returned.
This module has functions specific to processing RINEX.
Most notably, get_data(file) turns a standard RINEX file into
a GPSData object.

"""
# TODO:
# Confirm reading Rinex versions 2.0-2.09; read RINEX 3 / CRINEX 3;
# Support other RINEX file types (navigation message, meteorological data,
# clock date file).

import time
from itertools import zip_longest, repeat
from copy import deepcopy
from warnings import warn
from collections import namedtuple

from utility import fileread, listvalue, value
from gpstime import gpsdatetime
from gpsdata import GPSData

RNX_VER = '2.11'
CR_VER = '1.0'

truth = lambda x : 1

def btog(c):
    if c in (None, '', ' '):
        return 'G'
    return c.upper()

def toint(x):
    if x is None or x.strip() == '':
        return 0
    return int(x)

def choose(a, b):
    if a is not None and b in (' ', None):
        return a
    return b.replace('&', ' ')

def tofloat(x):
    if x is None or x.strip() == '':
        return 0.
    return float(x)

to3float = lambda line : tuple(tofloat(line[k*14:(k+1)*14]) for k in (0, 1, 2))

def delta2float(x):
    return x.days * 86400. + float(x.seconds) + x.microseconds / 1e9


def versioncheck(ver):
    """Given RINEX format version ver, verify that this program can handle it."""
    nums = ver.split('.')
    if not 0 < len(nums) < 3:
        raise ValueError('RINEX Version not parsable')
    elif int(nums[0]) != 2:
        raise IOError('RINEX File not version 2; unsupported')
    elif len(nums) > 1 and int(nums[1]) > 11:
        warn('RINEX minor version more recent than program.')
    return ver.strip()


def crxcheck(ver):
    """Check whether Compact RINEX version is known to this program."""
    if ver != '1.0':
        raise ValueError('CRINEX version ' + ver + ' not supported.')
    return ver.strip()


def iso(c):
    """Ensure that the character c is `O' (for RINEX observation data.)"""
    if c.upper() != 'O':
        raise IOError('RINEX File is not observation data')
    return c.upper()


def fullyear(year, baseyear):
    """Disambiguate two-digit year given a nearby full baseyear."""
# Rinex 2.12 specifies, in absence of baseyear, 80--99 mean 1980--1999,
# and 00--79 mean 2000--2079.
    if baseyear is None:
        baseyear = 2000
    base = int(baseyear)//100
    if (baseyear % 100) - year > 80:
# if the baseyear is 1999 and the 2-digit year is 00, its probably not 1900
        base += 1
    elif (baseyear % 100) - year <= -80:
# if base is 2000 and 2-digit is 99, probably not 2099
        base -= 1
    return year + base*100


def parseheadtime(line):
    """Parse RINEX time epoch, from headers, into gpsdatetime object."""
# ignores last of the seven digits after decimal point in RINEX seconds
    return gpsdatetime.strptime(line[:42], "  %Y    %m    %d    %H    %M   %S.%f")
# strptime doesn't actually pay attention to the number of spaces in the format string,
# but we specify the right number anyway...


def parsetime(line, baseyear):
    """Parse RINEX time epoch from observation data into gpsdatetime object.

    The source has two digit years which can be disambiguated with `baseyear'.
    """
    if not line.strip():
        return None
    year = fullyear(int(line[0:3]), baseyear)
    mdhms = time.strptime(line[3:18], " %m %d %H %M %S")
    usec = tofloat(line[18:26]) * 1000000
    return gpsdatetime(year, *mdhms[1:6], usec, None)


def wavelength(line, *, waveinfo={'G%02d' % prn : (1, 1) for prn in range(1, 33)}):
    """Parse RINEX WAVELENGTH FACT L1/2 headers

    These headers specify 1: Full cycle ambiguities (default),
    2: half cycle ambiguities (squaring), or 0: does not apply,
    either globally or for particular satellites.
    This is only valid for GPS satellites on frequencies L1 or L2.
    """
    # "waveinfo" is a persistent store of current ambiguities,
    # which is updated with each new header line. (it should not be passed)
    # If prn list is empty (numsats = 0), L1/2 ambiguity applies to all
    # satellites.  Otherwise, it applies to satellites given in the prnlist;
    # continuation lines are allowed.
    # Ambiguity information is valid until the next 'global' WAVELENGTH FACT,
    # or until that prn is reset.
    l1amb = toint(line[0:6])
    l2amb = toint(line[6:12])
    numsats = toint(line[12:18])
    if not numsats:  # This is a `global' line
        waveinfo.update(dict.fromkeys(waveinfo, (l1amb, l2amb)))
    else:
        for p in range(numsats):
            prn = btog(line[21 + 6 * p]) + '%02d' % toint(line[22 + 6 * p : 24 + 6 * p])
            waveinfo[prn] = (l1amb, l2amb)
    return waveinfo.copy()


class obscode:
    """Parse RINEX # / TYPES OF OBSERV headers, specifying observation types.

    These header list observation codes which will be listed in this file.
    Continuation lines are necessary for more than 9 observation types.
    It is possible to redefine this list in the course of a file.
    """
    # There must be `numtypes' many observation codes, possibly over two lines.
    # Continuation lines have blank `numtypes'.
    def __init__(self):
        self.numtypes = None

    def __call__(self, line):
        nt = toint(line[0:6])
        if self.numtypes is not None and not nt:  # continuation line
            if len(self.obstypes) >= self.numtypes:
                raise RuntimeError('Observation code headers seem broken.')
            for ot in range(min(self.numtypes - len(self.obstypes), 9)):
                self.obstypes += [line[6 * ot + 10 : 6 * ot + 12]]
        elif nt:
            self.numtypes = nt
            self.obstypes = []
            for ot in range(min(nt, 9)):
                self.obstypes += [line[6 * ot + 10 : 6 * ot + 12]]
        else:
            raise RuntimeError('Observation type code continuation header '
                               'without beginning!')
        return self.obstypes[:]


def satnumobs():
    """Parse RINEX PRN / # OF OBS headers.

    These headers list how many of each observation type were recorded for
    each satellite included in the file.  If present, there will be one for
    each satellite in the file (as reported in the # OF SATELLITES header.)
    If there are more than 9 observation types, a continuation line will be
    necessary for each satellite.
    This program will determine this information anyway, and check against the
    header if it is supplied.
    """
    sno = {}
    oprn = [None]

    def snoparse(line):
        """Return a dictionary, by satellite PRN code, of observation counts.

        The counts are a list in the same order as obscode().
        """
        prn = line[0:3]
        if prn.strip() == '' and oprn[0]:  # continuation line
            prn = oprn[0]
        elif prn.strip() != '':
            prn = btog(prn[0]) + '%02d' % toint(prn[1:])
            oprn[0] = prn
            if prn in sno:
                warn('Repeated # OF OBS for PRN ' + prn + ', why?')
            else:
                sno[prn] = []
        else:
            raise RuntimeError('PRN / # OF OBS continuation without beginning!')
        for no in range(9):
            obs = line[no * 6 + 3: no * 6 + 9]
            if obs.strip() == '':
                break
            else:
                sno[prn] += [toint(obs)]
        return sno


class header:
    """Header info for a given RINEX header type.
    
    Holds a list of field objects which are defined in the associated line.
    This is for header values which should only occur once.
    """
    field = namedtuple('field', ('name', 'start', 'stop', 'convert'))
    """A value in a RINEX header: variable name, position in the line, and how to interpret it."""
    field.__new__.__defaults__ = (str.strip,) # default "convert" function

    def __init__(self, field_args, multi_act=0):
        self.mems = [header.field(*fargs) for fargs in field_args]
        self.seen = None
        self.multi_act = multi_act
        # multi_act: What to do when encountering this value again.
        # 0 : replace and warn
        # 1 : disallow
        # 2 : replace

    @staticmethod
    def _fread(field, line):
        return value(field.convert(line[field.start:field.stop]))

    def read(self, meta, line, recordnum, lineno, epoch=None):
        label = line[60:]
        if self.seen is not None:
            if self.multi_act == 0:   # warn and replace
                warn('The header ' + label + ' was encountered multiple '
                     'times.  Old values clobbered.')
            elif self.multi_act == 1:  # forbidden
                raise ValueError('Header ' + label + ' occurs too often!')
            elif self.multi_act == 2:  # replace
                pass
            else:
                raise RuntimeError('Bad multiple-header action; fix RINEX')
        else:
            self.seen = recordnum
        for field in self.mems:
            meta[field.name] = self._fread(field, line)
            meta[field.name].recordnum = recordnum
            meta[field.name].lineno = lineno
            if epoch is not None:
                meta[field.name].epoch = epoch


class listheader(header):
    """This class is for header values which may occur several times.

    Each instance of the header is considered valid, and is stored.
    They are accessed as a list.
    """
    def read(self, meta, line, recordnum, lineno, epoch=None):
        for field in self.mems:
            if field.name not in meta:
                meta[field.name] = [self._fread(field,line)]
            else:
                meta[field.name] += [self._fread(field,line)]
            meta[field.name][-1].recordnum = recordnum
            meta[field.name][-1].lineno = lineno
            if epoch is not None:
                meta[field.name][-1].epoch = epoch


class listonce(header):
    """For header values which can only have one value at a time

    The value may change for different observation records.
    If multiple instances are at the same record number, the last is used.
    They are accessed by record number; whichever value is valid for that
    record is returned.
    """
    def read(self, meta, line, recordnum, lineno, epoch=None):
        for field in self.mems:
            if field.name not in meta:
                meta[field.name] = listvalue()
            meta[field.name][recordnum] = self._fread(field,line)
            meta[field.name][recordnum].recordnum = recordnum
            meta[field.name][recordnum].lineno = lineno
            if epoch is not None:
                meta[field.name][recordnum].epoch = epoch


RINEX = {
    'CRINEX VERS   / TYPE' : header((('crnxver', 0, 3, crxcheck),
                                     ('is_crx', 0, 0, truth))),
    'CRINEX PROG / DATE  ' : header((('crnxprog', 0, 20),
                                     ('crxdate', 40, 60),
                                     ('is_crx', 0, 0, truth))),
    'RINEX VERSION / TYPE' : header((('rnxver', 0, 9, versioncheck),
                                     ('filetype', 20, 21, iso),
                                     ('satsystem', 40, 41, btog))),
    'PGM / RUN BY / DATE ' : header((('rnxprog', 0, 20),
                                     ('agency', 20, 40),
                                     ('filedate', 40, 60))),
    'COMMENT             ' : listheader((('comment', 0, 60),)),
    'MARKER NAME         ' : listonce((('marker', 0, 60),)),
    # MARKER is a station, or receiving site.
    'MARKER NUMBER       ' : listonce((('markernum', 0, 20),)),
    'APPROX POSITION XYZ ' : listonce((('markerpos', 0, 42, to3float),)),
    # Position is in WGS84 frame.
    'OBSERVER / AGENCY   ' : header((('observer', 0, 20),
                                     ('obsagency', 20, 60))),
    'REC # / TYPE / VERS ' : header((('receivernum', 0, 20),
                                     ('receivertype', 20, 40),
                                     ('receiverver', 40, 60))),
    'ANT # / TYPE        ' : listonce((('antennanum', 0, 20),
                                       ('antennatype', 20, 40))),
    'ANTENNA: DELTA H/E/N' : listonce((('antennashift', 0, 42, to3float),)),
    # Up, East, North shift (meters) from marker position
    'WAVELENGTH FACT L1/2' : listonce((('ambiguity', 0, 53, wavelength),)),
    '# / TYPES OF OBSERV ' : listonce((('obscodes', 0, 60, obscode()),)),
    'INTERVAL            ' : listonce((('interval', 0, 10, tofloat),)),
    'TIME OF FIRST OBS   ' : header((('firsttime', 0, 43, parseheadtime),
                                     ('firsttimesys', 48, 51)), 1),
    'TIME OF LAST OBS    ' : header((('endtime', 0, 43, parseheadtime),
                                     ('endtimesys', 48, 51))),
    # End timesys must agree with first timesys.
    'RCV CLOCK OFFS APPL ' : listonce((('receiverclockcorrection', 0, 6, toint),)),
    'LEAP SECONDS        ' : listonce((('leapseconds', 0, 6, toint),)),
    '# OF SATELLITES     ' : header((('numsatellites', 0, 6, toint),)),
    'PRN / # OF OBS      ' : header((('obsnumpersatellite', 3, 60, satnumobs()),), 2),
#    'END OF HEADER       ' : header((), 1)
}


class recordLine:
    """Parse record headers (epoch lines) in standard RINEX.

    Combine continuation lines if necessary.
    """
    def __init__(self, baseyear):
        self.line = ''
        self.baseyear = baseyear
        self.epoch = None
        self.intervals = set()

    def update(self, fid):
        """Process a new epoch line."""
        self.line = self.getline(fid)
        self.oldepoch = self.epoch
        self.epoch = parsetime(self.line[0:26], self.baseyear)
        if self.epoch is not None and self.oldepoch is not None:
            self.intervals.add(delta2float(self.epoch - self.oldepoch))
        self.numrec = toint(self.line[29:32])
        self.flag = toint(self.line[28])

    def getline(self, fid):
        return fid.next()

    def prnlist(self, fid):
        """Return the list of PRNs (satellite IDs) included in this epoch line.

        May consume extra lines if there are more than 12 PRNs.
        """
        prnlist = []
        line = self.line
        for z in range(self.numrec):
            s = z % 12
            if z and not s:
                line = fid.next()
            prn = btog(line[32 + s * 3]) + '%02d' % toint(line[33 + 3*s : 35 + 3*s])
            prnlist += [prn]
        return prnlist

    def dataline(self, prn, numobs):
        return obsLine()

    def offset(self, fid):
        """Return receiver clock offset optionally included at end of epoch line."""
        return tofloat(self.line[68:])


class recordArc(recordLine):
    """Parse record headers in Compact RINEX.

    Each line only contains differences from the previous.
    """
    def __init__(self, baseyear):
        self.data = {}
        self.offsetval = None
        recordLine.__init__(self, baseyear)

    def getline(self, fid):
        self.offsetval = None
        line = fid.next()
        if line[0] == '&':
            return line.replace('&', ' ')
        else:
            return ''.join(choose(*ab) for ab in zip_longest(self.line, line))

    def prnlist(self, fid):
        prnlist = []
        for s in range(self.numrec):
            prn = btog(self.line[32 + s * 3]) + '%02d' % \
                                       toint(self.line[33 + s * 3 : 35 + s * 3])
            prnlist += [prn]
        return prnlist

    def dataline(self, prn, numobs):
        return self.data.setdefault(prn, obsArcs(numobs))

    def offset(self, fid):
        if self.offsetval is not None:
            return self.offsetval
        line = fid.next()
        if len(line) >= 2 and line[1] == '&':
            self.offsetArc = dataArc(toint(line[0]))
            self.offsetArc.update(toint(line[2:]))
        elif line.rstrip() and 'offsetArc' in self.__dict__:
            self.offsetArc.update(toint(line))
        elif line.rstrip():
            raise ValueError('Uninitialized clock offset data arc.')
        else:
            return 0.
        return self.offsetArc.get()//1000000000


class dataArc:
    """Numeric records in Compact RINEX are Nth-order differences from previous records.

    Difference order is usually 3. Fields are separated by space.
    LLI and STR are kept separately at the end of the line in one character
    each.
    """
    def __init__(self, order=3):
        self.order = order
        self.data = []
        self.index = 0

    def update(self, value):
        if self.index < self.order:
            self.data.append(value)
            self.index += 1
        else:
            self.data[self.order - 1] += value
        for diff in range(self.index - 2, -1, -1):
            self.data[diff] += self.data[diff + 1]
        return self.data[0]

    def get(self):
        if len(self.data):
            return self.data[0]
        else:
            return 0


class charArc:
    """Track an LLI or STR field in Compact RINEX.
    
    Only changes from the previous record are given; space indicates no change.
    """
    def __init__(self):
        self.data = '0'
    def update(self, char):
        self.data = ''.join(choose(*ab) for ab in zip_longest(self.data, char))
    def get(self):
        return toint(self.data)


class obsLine:
    """Read observations out of line(s) in a record in a standard RINEX file."""
    def update(self, fid):
        self.fid = fid
        self.ind = -1

    def next(self):
        self.ind = (self.ind + 1) % 5
        if not self.ind:
            self.line = self.fid.next()
        val = value(tofloat(self.line[self.ind * 16 : self.ind * 16 + 14]))
        LLI = toint(self.line[self.ind * 16 + 14 : self.ind * 16 + 15])
        STR = toint(self.line[self.ind * 16 + 15 : self.ind * 16 + 16])
        return (val, LLI, STR)

    __next__ = next

    def __iter__(self):
        return self


class obsArcs:
    """Calculate observations out of a line in a record in a compact RINEX file."""
    def __init__(self, numobs):
        self.numobs = numobs
        self.arcs = [dataArc() for n in range(numobs)]
        self.LLI = [charArc() for n in range(numobs)]
        self.STR = [charArc() for n in range(numobs)]

    def update(self, fid):
        line = fid.next()
        vals = line.split(' ', self.numobs)
        for c, v in enumerate(vals[:self.numobs]):
            if len(v) >= 2 and v[1] == '&':
                self.arcs[c] = dataArc(toint(v[0]))
                self.arcs[c].update(toint(v[2:]))
            elif v.rstrip():
                self.arcs[c].update(toint(v))
            elif v.rstrip():
                raise ValueError('Uninitialized data arc.')
        if len(vals) > self.numobs:
            for c, l in enumerate(vals[self.numobs][0:self.numobs*2:2]):
                self.LLI[c].update(l)
            for c, s in enumerate(vals[self.numobs][1:self.numobs*2:2]):
                self.STR[c].update(s)

    def __getitem__(self, ind):
        return (value(self.arcs[ind].get()/1000.), self.LLI[ind].get(),
                self.STR[ind].get())


def get_data(fid, is_crx=None):
    """Read data out of a RINEX 2.11 Observation Data File."""
    obsdata = GPSData()
    obspersat = {}
    rinex = deepcopy(RINEX)  # avoid `seen' records polluting other instances
    if hasattr(fid, 'name'):
        obsdata.meta['filename'] = fid.name
    fid = fileread(fid)
    procheader(fid, rinex, obsdata.meta, 0)
    baseyear = obsdata.timesetup()
    if is_crx or 'is_crx' in obsdata.meta:
        record = recordArc(baseyear)
    else:
        record = recordLine(baseyear)
    while True:
        try:
            record.update(fid)
        except StopIteration:
            break
        # Observations:         (prnlist, offset)
        #  0: normal
        #  1: Power failure occured since last record.
        # Header information:   (no prnlist, no offset)
        #  2: associated with start of antenna movement
        #  3: associated with occupation of new site (stop moving)
        #  4: nothing special
        #  5: External event: epoch is significant
        # 6: Cycle slip:        (prnlist, no offset)
        #  "same format as OBSERVATIONS records; slips instead of observation"
        #  What does that mean?
        if record.flag == 6:
            obsdata.breakphase(record.prnlist(fid))
            [fid.next() for ll in range(record.numrec)]  # ignore records
        elif record.flag == 5:
            procheader(fid, rinex, obsdata.meta, len(obsdata),
                       range(record.numrec), record.epoch)
        elif record.flag == 4:
            procheader(fid, rinex, obsdata.meta, len(obsdata),
                       range(record.numrec))
        elif 2 <= record.flag <= 3:
            obsdata.inmotion = record.flag == 2
            procheader(fid, rinex, obsdata.meta, len(obsdata),
                       range(record.numrec))
        elif 0 <= record.flag <= 1:
            obsdata.newrecord(record.epoch, powerfail=bool(record.flag), clockoffset=record.offset(fid))
            for prn in record.prnlist(fid):
                dataline = record.dataline(prn, len(obsdata.obscodes()))
                numobs = obspersat.setdefault(prn, {})
                dataline.update(fid)
                for obs, (val, LLI, STR) in zip(obsdata.obscodes(), dataline):
                    val.lostlock = bool(LLI % 2)
                    freq = toint(obs[1])
                    if prn[0] != 'G' or freq > 2:
                        val.wavefactor = 0
                    else:
                        if 'ambiguity' not in obsdata.meta:
                            ambig = 1
                        else:
                            ambig = obsdata.meta['ambiguity'][-1][prn][freq - 1]
                        if (LLI >> 1) % 2:
                            # wavelength factor opposite of currently set.
                            # By RINEX definition, valid only for GPS L1, L2
                            val.wavefactor = (ambig % 2) + 1
                        else:
                            val.wavefactor = ambig
                    val.antispoofing = bool((LLI >> 2) % 2)
                    val.strength = STR
                    obsdata.add(-1, prn, obs, val)
                    numobs[obs] = numobs.get(obs, 0) + 1
            obsdata.checkbreak()
    fid.close()
    obsdata.check(obspersat, record.intervals)
    return obsdata


def procheader(fid, RINEX, meta, recordnum, numlines=repeat(0), epoch=None):
    if isinstance(numlines, repeat) or numlines:
        meta.numblocks += 1
    for _ in numlines:
        try:
            line = fid.next().ljust(80)  # pad spaces to 80
        except StopIteration:
            break
        label = line[60:]
        if label == 'END OF HEADER       ':
            break
        elif label not in RINEX:
            for lbl in RINEX:
                if label.replace(' ', '') == lbl.replace(' ', ''):
                    warn('Label ' + label + ' recognized as ' + lbl
                         + ' despite incorrect whitespace.')
                    label = lbl
                    break
        if label in RINEX:
            RINEX[label].read(meta, line, recordnum, fid.lineno, epoch)
        else:
            warn('Header line ' + label + ' unrecognized; ignoring')
