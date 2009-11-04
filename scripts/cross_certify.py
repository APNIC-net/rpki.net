"""
Cross-certification tool to issue a new certificate based on an old
one that was issued by somebody else.  The point of the exercise is to
end up with a valid certificate in our own BPKI which has the same
subject name and subject public key as the one we're replacing.

Usage: python cross_certify.py { -i | --in     } input_cert
                               { -c | --ca     } issuing_cert
                               { -k | --key    } issuing_cert_key
                               { -s | --serial } serial_filename
                               [ { -h | --help } ]
                               [ { -o | --out  }     filename  (default: stdout)  ]
                               [ { -l | --lifetime } timedelta (default: 30 days) ]

$Id$

Copyright (C) 2009  Internet Systems Consortium ("ISC")

Permission to use, copy, modify, and distribute this software for any
purpose with or without fee is hereby granted, provided that the above
copyright notice and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND ISC DISCLAIMS ALL WARRANTIES WITH
REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY
AND FITNESS.  IN NO EVENT SHALL ISC BE LIABLE FOR ANY SPECIAL, DIRECT,
INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM
LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE
OR OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR
PERFORMANCE OF THIS SOFTWARE.

Portions copyright (C) 2007--2008  American Registry for Internet Numbers ("ARIN")

Permission to use, copy, modify, and distribute this software for any
purpose with or without fee is hereby granted, provided that the above
copyright notice and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND ARIN DISCLAIMS ALL WARRANTIES WITH
REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY
AND FITNESS.  IN NO EVENT SHALL ARIN BE LIABLE FOR ANY SPECIAL, DIRECT,
INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM
LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE
OR OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR
PERFORMANCE OF THIS SOFTWARE.
"""

import os, time, getopt, sys, POW, rpki.x509, rpki.sundial

os.environ["TZ"] = "UTC"
time.tzset()

def usage(errmsg = None):
  if errmsg is None:
    sys.stdout.write(__doc__)
    sys.exit(0)
  else:
    sys.stderr.write(errmsg + "\n" + __doc__)
    sys.exit(1)

child           = None
parent          = None
keypair         = None
serial_file     = None
lifetime        = rpki.sundial.timedelta(days = 30)
output          = None

opts, argv = getopt.getopt(sys.argv[1:], "h?i:o:c:k:s:l:",
                           ["help", "in=", "out=", "ca=",
                            "key=", "serial=", "lifetime="])
for o, a in opts:
  if o in ("-h", "--help", "-?"):
    usage()
  elif o in ("-i", "--in"):
    child = rpki.x509.X509(Auto_file = a)
  elif o in ("-o", "--out"):
    output = a
  elif o in ("-c", "--ca"):
    parent = rpki.x509.X509(Auto_file = a)
  elif o in ("-k", "--key"):
    keypair = rpki.x509.RSA(Auto_file = a)
  elif o in ("-s", "--serial"):
    serial_file = a
  elif o in ("-l", "--lifetime"):
    lifetime = rpki.sundial.timedelta.parse(a)

if argv:
  usage("Unused arguments: %r" % argv)
elif child is None:
  usage("--in not specified")
elif parent is None:
  usage("--ca not specified")
elif keypair is None:
  usage("--key not specified")
elif serial_file is None:
  usage("--serial not specified")

now = rpki.sundial.now()
notAfter = now + lifetime

try:
  f = open(serial_file, "r")
  serial = f.read()
  f.close()
  serial = int(serial.splitlines()[0], 16)
except IOError:
  serial = 1

cert = parent.cross_certify(keypair, child, serial, notAfter, now)

f = open(serial_file, "w")
f.write("%02x\n" % (serial + 1))
f.close()

if output is None:
  print cert.get_PEM()
else:
  f = open(output, "w")
  f.write(cert.get_PEM())
  f.close()
