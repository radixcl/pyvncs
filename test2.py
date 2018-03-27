#!/usr/bin/env python3
# -*- coding: utf-8 -*-

zlibHeader = bytearray()
zlibLength = 300309

zlibHeader.append( (zlibLength>>24) & 0xFF )
zlibHeader.append( (zlibLength>>16) & 0xFF )
zlibHeader.append( (zlibLength>>8) & 0xFF )
zlibHeader.append( zlibLength & 0xFF )

print(zlibHeader)
