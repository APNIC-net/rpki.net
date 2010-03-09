"""
Read an OpenSSL-style config file and a bunch of .csv files to find
out about parents and children and resources and ROA requests, oh my.
Run OpenSSL command line tool to construct BPKI certificates,
including cross-certification of other entities' BPKI certificates.

Package up all of the above as a single XML file which user can then
ship off to the IRBE.  If an XML file already exists, check it for
data coming back from the IRBE (principally PKCS #10 requests for our
BSC) and update it with current data.

The general idea here is that this one XML file contains all of the
data that needs to be exchanged as part of ordinary update operations;
each party updates it as necessary, then ships it to the other via
some secure channel: carrier pigeon, USB stick, gpg-protected email,
we don't really care.

This one program is written a little differently from all the other
Python RPKI programs.  This one program is intended to run as a
stand-alone script, without the other programs present.  It does
require a reasonably up-to-date version of the OpenSSL command line
tool (the one built as a side effect of building rcynic will do), but
it does -not- require POW or any Python libraries beyond what ships
with Python 2.5.  So this script uses xml.etree from the Python
standard libraries instead of lxml.etree, which sacrifices XML schema
validation support in favor of portability, and so forth.

To make things a little weirder, as a convenience to IRBE operators,
this script can itself be loaded as a Python module and invoked as
part of another program.  This requires a few minor contortions, but
avoids duplicating common code.

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
"""

# Only standard Python libraries for this program, please.

import subprocess, csv, re, os, getopt, sys, base64, glob, copy
import rpki.config

try:
  from lxml.etree import Element, SubElement, ElementTree
except ImportError:
  from xml.etree.ElementTree import Element, SubElement, ElementTree

# Our XML namespace and protocol version.

namespace      = "http://www.hactrn.net/uris/rpki/myrpki/"
version        = "1"
namespaceQName = "{" + namespace + "}"

# Dialect for our use of CSV files, here to make it easy to change if
# your site needs to do something different.  See doc for the csv
# module in the Python standard libraries for details if you need to
# customize this.

csv_dialect = csv.get_dialect("excel-tab")

# Whether to include incomplete entries when rendering to XML.

allow_incomplete = False

# Whether to whine about incomplete entries while rendering to XML.

whine = False

class comma_set(set):
  """
  Minor customization of set(), to provide a print syntax.
  """

  def __str__(self):
    return ",".join(self)

class EntityDB(object):
  """
  Wrapper for entitydb path lookups.  Hmm, maybe some or all of the
  entitydb glob stuff should end up here too?  Later.
  """

  def __init__(self, cfg):
    self.dir = cfg.get("entitydb_dir", "entitydb")

  def __call__(self, *args):
    return os.path.join(self.dir, *args)

  def iterate(self, *args):
    return glob.iglob(os.path.join(self.dir, *args))

class roa_request(object):
  """
  Representation of a ROA request.
  """

  v4re = re.compile("^([0-9]{1,3}\.){3}[0-9]{1,3}/[0-9]+(-[0-9]+)?$", re.I)
  v6re = re.compile("^([0-9a-f]{0,4}:){0,15}[0-9a-f]{0,4}/[0-9]+(-[0-9]+)?$", re.I)

  def __init__(self, asn, group):
    self.asn = asn
    self.group = group
    self.v4 = comma_set()
    self.v6 = comma_set()

  def __repr__(self):
    s = "<%s asn %s group %s" % (self.__class__.__name__, self.asn, self.group)
    if self.v4:
      s += " v4 %s" % self.v4
    if self.v6:
      s += " v6 %s" % self.v6
    return s + ">"

  def add(self, prefix):
    """
    Add one prefix to this ROA request.
    """
    if self.v4re.match(prefix):
      self.v4.add(prefix)
    elif self.v6re.match(prefix):
      self.v6.add(prefix)
    else:
      raise RuntimeError, "Bad prefix syntax: %r" % (prefix,)

  def xml(self, e):
    """
    Generate XML element represeting representing this ROA request.
    """
    e = SubElement(e, "roa_request",
                   asn = self.asn,
                   v4 = str(self.v4),
                   v6 = str(self.v6))
    e.tail = "\n"

class roa_requests(dict):
  """
  Database of ROA requests.
  """

  def add(self, asn, group, prefix):
    """
    Add one <ASN, group, prefix> set to ROA request database.
    """
    key = (asn, group)
    if key not in self:
      self[key] = roa_request(asn, group)
    self[key].add(prefix)

  def xml(self, e):
    """
    Render ROA requests as XML elements.
    """
    for r in self.itervalues():
      r.xml(e)

  @classmethod
  def from_csv(cls, roa_csv_file):
    """
    Parse ROA requests from CSV file.
    """
    self = cls()
    # format:  p/n-m asn group
    for pnm, asn, group in csv_open(roa_csv_file):
      self.add(asn = asn, group = group, prefix = pnm)
    return self

class child(object):
  """
  Representation of one child entity.
  """

  v4re = re.compile("^(([0-9]{1,3}\.){3}[0-9]{1,3}/[0-9]+)|(([0-9]{1,3}\.){3}[0-9]{1,3}-([0-9]{1,3}\.){3}[0-9]{1,3})$", re.I)
  v6re = re.compile("^(([0-9a-f]{0,4}:){0,15}[0-9a-f]{0,4}/[0-9]+)|(([0-9a-f]{0,4}:){0,15}[0-9a-f]{0,4}-([0-9a-f]{0,4}:){0,15}[0-9a-f]{0,4})$", re.I)

  def __init__(self, handle):
    self.handle = handle
    self.asns = comma_set()
    self.v4 = comma_set()
    self.v6 = comma_set()
    self.validity = None
    self.bpki_certificate = None

  def __repr__(self):
    s = "<%s %s" % (self.__class__.__name__, self.handle)
    if self.asns:
      s += " asn %s" % self.asns
    if self.v4:
      s += " v4 %s" % self.v4
    if self.v6:
      s += " v6 %s" % self.v6
    if self.validity:
      s += " valid %s" % self.validity
    if self.bpki_certificate:
      s += " cert %s" % self.bpki_certificate
    return s + ">"

  def add(self, prefix = None, asn = None, validity = None, bpki_certificate = None):
    """
    Add prefix, autonomous system number, validity date, or BPKI
    certificate for this child.
    """
    if prefix is not None:
      if self.v4re.match(prefix):
        self.v4.add(prefix)
      elif self.v6re.match(prefix):
        self.v6.add(prefix)
      else:
        raise RuntimeError, "Bad prefix syntax: %r" % (prefix,)
    if asn is not None:
      self.asns.add(asn)
    if validity is not None:
      self.validity = validity
    if bpki_certificate is not None:
      self.bpki_certificate = bpki_certificate

  def xml(self, e):
    """
    Render this child as an XML element.
    """
    complete = self.bpki_certificate and self.validity
    if whine and not complete:
      print "Incomplete child entry %s" % self
    if complete or allow_incomplete:
      e = SubElement(e, "child",
                     handle = self.handle,
                     valid_until = self.validity,
                     asns = str(self.asns),
                     v4 = str(self.v4),
                     v6 = str(self.v6))
      e.tail = "\n"
      if self.bpki_certificate:
        PEMElement(e, "bpki_certificate", self.bpki_certificate)

class children(dict):
  """
  Database of children.
  """

  def add(self, handle, prefix = None, asn = None, validity = None, bpki_certificate = None):
    """
    Add resources to a child, creating the child object if necessary.
    """
    if handle not in self:
      self[handle] = child(handle)
    self[handle].add(prefix = prefix, asn = asn, validity = validity, bpki_certificate = bpki_certificate)

  def xml(self, e):
    """
    Render children database to XML.
    """
    for c in self.itervalues():
      c.xml(e)

  @classmethod
  def from_csv(cls, children_csv_file, prefix_csv_file, asn_csv_file, fxcert, entitydb):
    """
    Parse child resources, certificates, and validity dates from CSV files.
    """
    self = cls()
    for f in entitydb.iterate("children", "*.xml"):
      c = etree_read(f)
      self.add(handle = os.path.splitext(os.path.split(f)[-1])[0],
               validity = c.get("valid_until"),
               bpki_certificate = fxcert(c.findtext("bpki_child_ta")))
    # childname p/n
    for handle, pn in csv_open(prefix_csv_file):
      self.add(handle = handle, prefix = pn)
    # childname asn
    for handle, asn in csv_open(asn_csv_file):
      self.add(handle = handle, asn = asn)
    return self

class parent(object):
  """
  Representation of one parent entity.
  """

  def __init__(self, handle):
    self.handle = handle
    self.service_uri = None
    self.bpki_cms_certificate = None
    self.bpki_https_certificate = None
    self.myhandle = None
    self.sia_base = None

  def __repr__(self):
    s = "<%s %s" % (self.__class__.__name__, self.handle)
    if self.myhandle:
      s += " myhandle %s" % self.myhandle
    if self.service_uri:
      s += " uri %s" % self.service_uri
    if self.sia_base:
      s += " sia %s" % self.sia_base
    if self.bpki_cms_certificate:
      s += " cms %s" % self.bpki_cms_certificate
    if self.bpki_https_certificate:
      s += " https %s" % self.bpki_https_certificate
    return s + ">"

  def add(self, service_uri = None,
          bpki_cms_certificate = None,
          bpki_https_certificate = None,
          myhandle = None,
          sia_base = None):
    """
    Add service URI or BPKI certificates to this parent object.
    """
    if service_uri is not None:
      self.service_uri = service_uri
    if bpki_cms_certificate is not None:
      self.bpki_cms_certificate = bpki_cms_certificate
    if bpki_https_certificate is not None:
      self.bpki_https_certificate = bpki_https_certificate
    if myhandle is not None:
      self.myhandle = myhandle
    if sia_base is not None:
      self.sia_base = sia_base

  def xml(self, e):
    """
    Render this parent object to XML.
    """
    complete = self.bpki_cms_certificate and self.bpki_https_certificate and self.myhandle and self.service_uri and self.sia_base
    if whine and not complete:
      print "Incomplete parent entry %s" % self
    if complete or allow_incomplete:
      e = SubElement(e, "parent",
                     handle = self.handle,
                     myhandle = self.myhandle,
                     service_uri = self.service_uri,
                     sia_base = self.sia_base)
      e.tail = "\n"
      if self.bpki_cms_certificate:
        PEMElement(e, "bpki_cms_certificate", self.bpki_cms_certificate)
      if self.bpki_https_certificate:
        PEMElement(e, "bpki_https_certificate", self.bpki_https_certificate)

class parents(dict):
  """
  Database of parent objects.
  """

  def add(self, handle,
          service_uri = None,
          bpki_cms_certificate = None,
          bpki_https_certificate = None,
          myhandle = None,
          sia_base = None):
    """
    Add service URI or certificates to parent object, creating it if necessary.
    """
    if handle not in self:
      self[handle] = parent(handle)
    self[handle].add(service_uri = service_uri,
                     bpki_cms_certificate = bpki_cms_certificate,
                     bpki_https_certificate = bpki_https_certificate,
                     myhandle = myhandle,
                     sia_base = sia_base)

  def xml(self, e):
    for c in self.itervalues():
      c.xml(e)

  @classmethod
  def from_csv(cls, parents_csv_file, fxcert, entitydb):
    """
    Parse parent data from CSV file.
    """
    self = cls()
    for f in entitydb.iterate("parents", "*.xml"):
      h = os.path.splitext(os.path.split(f)[-1])[0]
      p = etree_read(f)
      r = etree_read(f.replace(os.path.sep + "parents"      + os.path.sep,
                               os.path.sep + "repositories" + os.path.sep))
      assert r.get("type") == "confirmed"
      self.add(handle = h,
               service_uri = p.get("service_uri"),
               bpki_cms_certificate = fxcert(p.findtext("bpki_resource_ta")),
               bpki_https_certificate = fxcert(p.findtext("bpki_server_ta")),
               myhandle = p.get("child_handle"),
               sia_base = r.get("sia_base"))
    return self

def csv_open(filename):
  """
  Open a CSV file, with settings that make it a tab-delimited file.
  You may need to tweak this function for your environment, see the
  csv module in the Python standard libraries for details.
  """
  return csv.reader(open(filename, "rb"), dialect = csv_dialect)

def PEMElement(e, tag, filename, **kwargs):
  """
  Create an XML element containing Base64 encoded data taken from a
  PEM file.
  """
  lines = open(filename).readlines()
  while lines:
    if lines.pop(0).startswith("-----BEGIN "):
      break
  while lines:
    if lines.pop(-1).startswith("-----END "):
      break
  if e.text is None:
    e.text = "\n"
  se = SubElement(e, tag, **kwargs)
  se.text = "\n" + "".join(lines)
  se.tail = "\n"
  return se

class CA(object):
  """
  Representation of one certification authority.
  """

  # Mapping of path restriction values we use to OpenSSL config file
  # section names.

  path_restriction = { 0 : "ca_x509_ext_xcert0",
                       1 : "ca_x509_ext_xcert1" }

  def __init__(self, cfg, dir):
    self.cfg    = cfg
    self.dir    = dir
    self.cer    = dir + "/ca.cer"
    self.key    = dir + "/ca.key"
    self.req    = dir + "/ca.req"
    self.crl    = dir + "/ca.crl"
    self.index  = dir + "/index"
    self.serial = dir + "/serial"
    self.crlnum = dir + "/crl_number"

    self.env = { "PATH" : os.environ["PATH"],
                 "BPKI_DIRECTORY" : dir,
                 "RANDFILE" : ".OpenSSL.whines.unless.I.set.this" }

  def run_ca(self, *args):
    """
    Run OpenSSL "ca" command with tailored environment variables and common initial
    arguments.  "ca" is rather chatty, so we suppress its output except on errors.
    """
    cmd = (openssl, "ca", "-batch", "-config",  self.cfg) + args
    p = subprocess.Popen(cmd, env = self.env, stdout = subprocess.PIPE, stderr = subprocess.STDOUT)
    log = p.communicate()[0]
    if p.wait() != 0:
      sys.stderr.write(log)
      raise subprocess.CalledProcessError(returncode = p.returncode, cmd = cmd)

  def run_req(self, key_file, req_file):
    """
    Run OpenSSL "req" command with tailored environment variables and common arguments.
    "req" is rather chatty, so we suppress its output except on errors.
    """
    if not os.path.exists(key_file) or not os.path.exists(req_file):
      cmd = (openssl, "req", "-new", "-sha256", "-newkey", "rsa:2048",
             "-config", self.cfg, "-keyout", key_file, "-out", req_file)
      p = subprocess.Popen(cmd, env = self.env, stdout = subprocess.PIPE, stderr = subprocess.STDOUT)
      log = p.communicate()[0]
      if p.wait() != 0:
        sys.stderr.write(log)
        raise subprocess.CalledProcessError(returncode = p.returncode, cmd = cmd)

  @staticmethod
  def touch_file(filename, content = None):
    """
    Create dumb little text files expected by OpenSSL "ca" utility.
    """
    if not os.path.exists(filename):
      f = open(filename, "w")
      if content is not None:
        f.write(content)
      f.close()

  def setup(self, ca_name):
    """
    Set up this CA.  ca_name is an X.509 distinguished name in
    /tag=val/tag=val format.
    """

    modified = False

    if not os.path.exists(self.dir):
      os.makedirs(self.dir)
      self.touch_file(self.index)
      self.touch_file(self.serial, "01\n")
      self.touch_file(self.crlnum, "01\n")

    self.run_req(key_file = self.key, req_file = self.req)

    if not os.path.exists(self.cer):
      modified = True
      self.run_ca("-selfsign", "-extensions", "ca_x509_ext_ca", "-subj", ca_name, "-in", self.req, "-out", self.cer)

    if not os.path.exists(self.crl):
      modified = True
      self.run_ca("-gencrl", "-out", self.crl)

    return modified

  def ee(self, ee_name, base_name):
    """
    Issue an end-enity certificate.
    """
    key_file = "%s/%s.key" % (self.dir, base_name)
    req_file = "%s/%s.req" % (self.dir, base_name)
    cer_file = "%s/%s.cer" % (self.dir, base_name)
    self.run_req(key_file = key_file, req_file = req_file)
    if not os.path.exists(cer_file):
      self.run_ca("-extensions", "ca_x509_ext_ee", "-subj", ee_name, "-in", req_file, "-out", cer_file)
      return True
    else:
      return False

  def bsc(self, pkcs10):
    """
    Issue BSC certificiate, if we have a PKCS #10 request for it.
    """

    if pkcs10 is None:
      return None, None
    
    pkcs10 = base64.b64decode(pkcs10)

    assert pkcs10

    cmd = (openssl, "dgst", "-md5")
    p = subprocess.Popen(cmd, stdin = subprocess.PIPE, stdout = subprocess.PIPE)
    hash = p.communicate(pkcs10)[0].strip()
    if p.wait() != 0:
      raise subprocess.CalledProcessError(returncode = p.returncode, cmd = cmd)

    req_file = "%s/bsc.%s.req" % (self.dir, hash)
    cer_file = "%s/bsc.%s.cer" % (self.dir, hash)

    if not os.path.exists(cer_file):

      cmd = (openssl, "req", "-inform", "DER", "-out", req_file)
      p = subprocess.Popen(cmd, stdin = subprocess.PIPE)
      p.communicate(pkcs10)
      if p.wait() != 0:
        raise subprocess.CalledProcessError(returncode = p.returncode, cmd = cmd)

      self.run_ca("-extensions", "ca_x509_ext_ee", "-in", req_file, "-out", cer_file)

    return req_file, cer_file

  def fxcert(self, b64, filename = None, path_restriction = 0):
    """
    Write PEM certificate to file, then cross-certify.
    """
    fn = os.path.join(self.dir, filename or "temp.%s.cer" % os.getpid())
    try:
      cmd = (openssl, "x509", "-inform", "DER", "-out", fn)
      p = subprocess.Popen(cmd, stdin = subprocess.PIPE)
      p.communicate(base64.b64decode(b64))
      if p.wait() != 0:
        raise subprocess.CalledProcessError(returncode = p.returncode, cmd = cmd)
      return self.xcert(fn, path_restriction)
    finally:
      if not filename and os.path.exists(fn):
        #os.unlink(fn)
        pass

  def xcert(self, cert, path_restriction = 0):
    """
    Cross-certify a certificate represented as a PEM file.
    """

    if not cert:
      return None

    if not os.path.exists(cert):
      #print "Certificate %s doesn't exist, skipping" % cert
      return None

    # Extract public key and subject name from PEM file and hash it so
    # we can use the result as a tag for cross-certifying this cert.

    cmd1 = (openssl, "x509", "-noout", "-pubkey", "-subject", "-in", cert)
    cmd2 = (openssl, "dgst", "-md5")

    p1 = subprocess.Popen(cmd1, stdout = subprocess.PIPE)
    p2 = subprocess.Popen(cmd2, stdin = p1.stdout, stdout = subprocess.PIPE)

    xcert = "%s/xcert.%s.cer" % (self.dir, p2.communicate()[0].strip())

    if p1.wait() != 0:
      raise subprocess.CalledProcessError(returncode = p1.returncode, cmd = cmd1)
    if p2.wait() != 0:
      raise subprocess.CalledProcessError(returncode = p2.returncode, cmd = cmd2)

    # Cross-certify the cert we were given, if we haven't already.
    # This only works for self-signed certs, due to limitations of the
    # OpenSSL command line tool, but that suffices for our purposes.

    if not os.path.exists(xcert):
      self.run_ca("-ss_cert", cert, "-out", xcert, "-extensions", self.path_restriction[path_restriction])

    return xcert

def etree_validate(e):
  try:
    import schema
    schema.myrpki.assertValid(e)
  except lxml.etree.DocumentInvalid:
    print lxml.etree.tostring(e, pretty_print = True)
    raise
  except ImportError:
    print "Couldn't import RelaxNG schema, validation disabled"

def etree_write(e, filename, verbose = True, validate = False):
  """
  Write out an etree to a file, safely.

  I still miss SYSCAL(RENMWO).
  """
  assert isinstance(filename, str)
  if verbose:
    print "Writing", filename
  e = copy.deepcopy(e)
  e.set("version", version)
  for i in e.getiterator():
    if i.tag[0] != "{":
      i.tag = namespaceQName + i.tag
    assert i.tag.startswith(namespaceQName)
  if validate:
    etree_validate(e)
  ElementTree(e).write(filename + ".tmp")
  os.rename(filename + ".tmp", filename)

def etree_read(filename, verbose = False, validate = False):
  """
  Read an etree from a file, verifying then stripping XML namespace
  cruft.
  """
  if verbose:
    print "Reading", filename
  e = ElementTree(file = filename).getroot()
  if validate:
    etree_validate(e)
  for i in e.getiterator():
    if i.tag.startswith(namespaceQName):
      i.tag = i.tag[len(namespaceQName):]
    else:
      raise RuntimeError, "XML tag %r is not in namespace %r" % (i.tag, namespace)
  return e

def main(argv = ()):
  """
  Main program.  Must be callable from other programs as well as being
  invoked directly when this module is run as a script.
  """

  cfg_file = "myrpki.conf"

  opts, argv = getopt.getopt(argv, "c:h:?", ["config=", "help"])
  for o, a in opts:
    if o in ("-h", "--help", "-?"):
      print __doc__
      sys.exit(0)
    elif o in ("-c", "--config"):
      cfg_file = a
  if argv:
    raise RuntimeError, "Unexpected arguments %r" % (argv,)

  cfg = rpki.config.parser(cfg_file, "myrpki")

  my_handle                     = cfg.get("handle")
  roa_csv_file                  = cfg.get("roa_csv")
  children_csv_file             = cfg.get("children_csv")
  parents_csv_file              = cfg.get("parents_csv")
  prefix_csv_file               = cfg.get("prefix_csv")
  asn_csv_file                  = cfg.get("asn_csv")
  bpki_dir                      = cfg.get("bpki_resources_directory")
  xml_filename                  = cfg.get("xml_filename")
  repository_bpki_certificate   = cfg.get("repository_bpki_certificate")
  repository_handle             = cfg.get("repository_handle")

  global openssl
  openssl = cfg.get("openssl", "openssl")

  entitydb = EntityDB(cfg)

  bpki = CA(cfg_file, bpki_dir)

  try:
    bsc_req, bsc_cer = bpki.bsc(etree_read(xml_filename).findtext("bpki_bsc_pkcs10"))
  except IOError:
    bsc_req, bsc_cer = None, None

  e = Element("myrpki", handle = my_handle, repository_handle = repository_handle)

  roa_requests.from_csv(roa_csv_file).xml(e)

  children.from_csv(
    children_csv_file = children_csv_file,
    prefix_csv_file = prefix_csv_file,
    asn_csv_file = asn_csv_file,
    fxcert = bpki.fxcert,
    entitydb = entitydb).xml(e)

  parents.from_csv(
    parents_csv_file = parents_csv_file,
    fxcert = bpki.fxcert,
    entitydb = entitydb).xml(e)

  PEMElement(e, "bpki_ca_certificate", bpki.cer)
  PEMElement(e, "bpki_crl",            bpki.crl)

  if os.path.exists(repository_bpki_certificate):
    PEMElement(e, "bpki_repository_certificate", bpki.xcert(repository_bpki_certificate))

  if bsc_cer:
    PEMElement(e, "bpki_bsc_certificate", bsc_cer)

  if bsc_req:
    PEMElement(e, "bpki_bsc_pkcs10", bsc_req)

  etree_write(e, xml_filename)

# When this file is run as a script, run main() with command line
# arguments.  main() can't use sys.argv directly as that might be the
# command line for some other program that loads this module.

if __name__ == "__main__":
  main(sys.argv[1:])
