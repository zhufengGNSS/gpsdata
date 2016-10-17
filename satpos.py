from fetchnav import getsp3file
from utility import fileread
from gpstime import getutctime, gpsdatetime
from numbers import Number
import re
import numpy as np


sp3head = [(r'#[abc][PV]', 1),
           (r'##', 1),
           (r'\+ ', 5),
           (r'\+\+', 5),
           (r'%c [MG]  cc GPS', 1),
           (r'%c', 1),
           (r'%f', 2), 
           (r'%i', 2),
           (r'/\*', 4)]
"""The leading characters of the 22 header lines. We check that they match
but otherwise ignore the header entirely."""

class posrecord(dict):
    """A record of satellite positions at a given epoch.

    Has field epoch in addition to being a dictionary (by PRN code) of XYZ tuples.
    Can access as record.epoch, record[13], record['G17'], or iteration.
    """
    def __init__(self, epoch):
        self.epoch = epoch

    def __getitem__(self, index):
        """Allow you to access GPS satellites, eg record['G13'], as
        simply record[13].  For GLONASS or Galileo, you must use the full code.
        """
        if index == 'epoch':
            return self.epoch
        if isinstance(index, Number):
            return dict.__getitem__(self, 'G%02d' % index)
        return dict.__getitem__(self, index)

    def __contains__(self, index):
        """Allow containment tests (eg if 13 in record:) for abbreviated GPS PRNs."""
        if isinstance(index, (int, long, float)):
            return dict.__contains__(self, 'G%02d' % index)
        return dict.__contains__(self, index)

def procheader(fid):        
    for cc, num in sp3head:
        for _ in range(num):
            ll = fid.next()
            if not re.match(cc, ll):
                raise ValueError(fid.name + ' does not have valid sp3 header lines (line '
                        + str(fid.lineno) + ' begins ' + ll[:len(cc)] + '; '
                        'we expected ' + cc + ').')

def gps_second(epline):
    """Convert an epoch header line to seconds from the gps epoch.
    
    The value is a Python float, which has a resolution of roughly one microsecond
    when the value is around a billion (ca. 2016)"""
    dt = gpsdatetime.strptime(epline[:29], "*  %Y %m %d %H %M %S.%f")
    return (dt - gpsdatetime()).total_seconds()
    
def addpos(rec, pline):
    prn = pline[1:4]
    x = float(pline[4:18])
    y = float(pline[18:32])
    z = float(pline[32:46])
    rec[prn] = (x, y, z)

def readsp3(filename):
    """List of (x,y,z) tuples from the sp3 file."""
    fid = fileread(filename)
    procheader(fid)
    poslist = []
# epoch lines begin with '*'. Position lines begin with 'P'.
# Velocity lines begin with 'V' (ignored); correlation lines begin with 'E' (ignored).
# (last line is 'EOF').
    for line in fid:
        if line[0] in ('E', 'V'):
            continue
        elif line[0] == '*':
            poslist.append(posrecord(gps_second(line)))
        elif line[0] == 'P':
            addpos(poslist[-1], line)
        else:
            print('Unrecognized line in sp3 file ' + filename + ':\n' + line
                    + '\nIgnoring...')
    return poslist

def rot3(vector, angle):
    """Rotate vector by angle around z-axis"""
    rotmat = np.array([[ np.cos(angle), np.sin(angle), 0],
                       [-np.sin(angle), np.cos(angle), 0],
                       [             0,             0, 1]])
    return rotmat @ vector

def near_indices(t, tow, n=7):
    """The indices of the n closest times to t"""
    return np.sort(np.argsort(np.abs(tow-t))[:n])

def sp3_interpolator(t, tow, xyz):
# This function modified from code by Ryan Hardy
    n = len(tow)
    omega = 2*2*np.pi/86164.090530833 # 4π/mean sidereal day
    independent = np.zeros((n, n))

    tinterp = tow - np.median(tow)
    for j in range(-(n-1)//2, (n-1)//2+1):
        independent[j] = np.cos(np.abs(j)*omega*tinterp - (j > 0)*np.pi/2)
    xyzr = [rot3(xyz[j], omega/2*tinterp[j]) for j in range(n)]
     
    independent = independent.T
    eig =  np.linalg.eig(independent)
    iinv  = (eig[1] * 1/eig[0] @ np.linalg.inv(eig[1]))

    coeffs = iinv @ xyzr
    j = np.arange(-(n-1)//2, (n-1)//2 + 1)
    tx = t - np.median(tow)
    r_inertial =  np.sum(coeffs[j].T * np.cos(np.abs(j)*omega*tx - (j > 0)*np.pi/2), -1)
    return rot3(r_inertial, -omega/2*tx)

def satpos(poslist, prn, sec):
    """Compute position of GPS satellite with given prn # at given GPS second.

    Return X, Y, Z cartesian coordinates, in km, Earth-Centered Earth-Fixed.
    GPS second is total seconds since the GPS epoch (float).
    """
# Just a dumb wrapper for sp3_interpolator for now
    tow = np.array([p.epoch for p in poslist])
    m = near_indices(sec, tow)
    xyz = np.array([poslist[k][prn] for k in m])
    return sp3_interpolator(sec, tow[m], xyz)

    