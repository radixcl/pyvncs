# coding=utf-8
# pyvncs
# Copyright (C) 2017-2018 Matias Fernandez
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU Lesser General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option) any
# later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU Lesser General Public License for more
# details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

import os

# Disabled encodings: comma-separated list (e.g. "hextile,tight")
_DISABLED_ENCODINGS = os.environ.get('PYVNCS_DISABLED_ENCODINGS', '').lower().split(',')
_DISABLED_ENCODINGS = [e.strip() for e in _DISABLED_ENCODINGS if e.strip()]

# at least, raw encoding is needed by the rfb protocol    
from . import common
from . import raw
from . import zlib
#from . import zrle
if 'tight' not in _DISABLED_ENCODINGS:
    from . import tight
if 'hextile' not in _DISABLED_ENCODINGS:
    from . import hextile
from . import cursor
