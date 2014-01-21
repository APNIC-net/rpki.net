/*
 * Copyright (C) 2014  Dragon Research Labs ("DRL")
 * Portions copyright (C) 2006--2008  American Registry for Internet Numbers ("ARIN")
 * 
 * Permission to use, copy, modify, and distribute this software for any
 * purpose with or without fee is hereby granted, provided that the above
 * copyright notices and this permission notice appear in all copies.
 * 
 * THE SOFTWARE IS PROVIDED "AS IS" AND DRL AND ARIN DISCLAIM ALL
 * WARRANTIES WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED
 * WARRANTIES OF MERCHANTABILITY AND FITNESS.  IN NO EVENT SHALL DRL OR
 * ARIN BE LIABLE FOR ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL
 * DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM LOSS OF USE, DATA
 * OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER
 * TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR
 * PERFORMANCE OF THIS SOFTWARE.
 */

/* $Id$ */

#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>
#include <unistd.h>
#include <string.h>
#include <dirent.h>
#include <limits.h>

#include <openssl/bio.h>
#include <openssl/pem.h>
#include <openssl/err.h>
#include <openssl/x509.h>
#include <openssl/x509v3.h>
#include <openssl/safestack.h>
#include <openssl/conf.h>
#include <openssl/rand.h>
#include <openssl/asn1.h>
#include <openssl/asn1t.h>
#include <openssl/cms.h>

#include <rpki/roa.h>

#ifndef FILENAME_MAX
#define	FILENAME_MAX		1024
#endif

#ifndef ADDR_RAW_BUF_LEN
#define ADDR_RAW_BUF_LEN	16
#endif



/*
 * Error handling.
 */

#define _lose(_msg_, _file_)							\
  do {										\
    if (_file_)									\
      fprintf(stderr, "%s:%d: %s: %s\n", __FILE__, __LINE__, _msg_, _file_);	\
    else									\
      fprintf(stderr, "%s:%d: %s\n", __FILE__, __LINE__, _msg_);		\
    fprintf(stderr, "%s: %s\n", _msg_, _file_);					\
  } while (0)

#define lose(_msg_, _file_)			\
  do {						\
    _lose(_msg_, _file_);			\
    goto done;					\
  } while (0)
 
#define lose_errno(_msg_, _file_)		\
  do {						\
    _lose(_msg_, _file_);			\
    perror(NULL);				\
    goto done;					\
  } while (0)
 
#define lose_openssl(_msg_, _file_)		\
  do {						\
    _lose(_msg_, _file_);			\
    ERR_print_errors_fp(stderr);		\
    goto done;					\
  } while (0)


/*
 * Extract a ROA prefix from the ASN.1 bitstring encoding.
 */
static int extract_roa_prefix(unsigned char *addr,
			      unsigned *prefixlen,
			      const ASN1_BIT_STRING *bs,
			      const unsigned afi)
{
  unsigned length;

  switch (afi) {
  case IANA_AFI_IPV4: length =  4; break;
  case IANA_AFI_IPV6: length = 16; break;
  default: return 0;
  }

  if (bs->length < 0 || bs->length > length)
    return 0;

  if (bs->length > 0) {
    memcpy(addr, bs->data, bs->length);
    if ((bs->flags & 7) != 0) {
      unsigned char mask = 0xFF >> (8 - (bs->flags & 7));
      addr[bs->length - 1] &= ~mask;
    }
  }

  memset(addr + bs->length, 0, length - bs->length);

  *prefixlen = (bs->length * 8) - (bs->flags & 7);

  return 1;
}

/*
 * Check str for a trailing suffix.
 */
static int has_suffix(const char *str, const char *suffix)
{
  size_t len_str, len_suffix;
  assert(str != NULL && suffix != NULL);
  len_str = strlen(str);
  len_suffix = strlen(suffix);
  return len_str >= len_suffix && !strcmp(str + len_str - len_suffix, suffix);
}

/*
 * Handle one object.
 */
static void file_handler(const char *filename, const unsigned prefix_afi, const unsigned char *prefix, const unsigned long prefixlen)
{
  unsigned char roa_prefix[ADDR_RAW_BUF_LEN];
  unsigned roa_prefixlen, roa_maxprefixlen, plen;
  CMS_ContentInfo *cms = NULL;
  BIO *b = NULL;
  ROA *r = NULL;
  int i, j, k, n;
  unsigned long asid;

  if (!(b = BIO_new_file(filename, "rb")))
    lose_openssl("Couldn't open CMS file", filename);

  if ((cms = d2i_CMS_bio(b, NULL)) == NULL)
    lose_openssl("Couldn't read CMS file", filename);

  BIO_free(b);

  if ((b = BIO_new(BIO_s_mem())) == NULL)
    lose_openssl("Couldn't open ROA", filename);

  if (CMS_verify(cms, NULL, NULL, NULL, b, CMS_NOCRL | CMS_NO_SIGNER_CERT_VERIFY | CMS_NO_ATTR_VERIFY | CMS_NO_CONTENT_VERIFY) <= 0)
    lose_openssl("Couldn't parse ROA CMS", filename);

  if ((r = ASN1_item_d2i_bio(ASN1_ITEM_rptr(ROA), b, NULL)) == NULL)
    lose_openssl("Couldn't parse ROA", filename);

  asid = (unsigned long) ASN1_INTEGER_get(r->asID);

  for (i = 0; i < sk_ROAIPAddressFamily_num(r->ipAddrBlocks); i++) {
    ROAIPAddressFamily *f = sk_ROAIPAddressFamily_value(r->ipAddrBlocks, i);

    /*
     * AFI must match, SAFI must be null
     */
    if (f->addressFamily->length != 2 ||
	prefix_afi != ((f->addressFamily->data[0] << 8) | (f->addressFamily->data[1])))
      continue;

    for (j = 0; j < sk_ROAIPAddress_num(f->addresses); j++) {
      ROAIPAddress *a = sk_ROAIPAddress_value(f->addresses, j);

      if (!extract_roa_prefix(roa_prefix, &roa_prefixlen, a->IPAddress, prefix_afi))
	lose("Malformed ROA", filename);

      /*
       * If the prefix we're looking for is bigger than the ROA
       * prefix, the ROA can't possibly cover.
       */
      if (prefixlen < roa_prefixlen)
	continue;

      if (a->maxLength)
	roa_maxprefixlen = ASN1_INTEGER_get(a->maxLength);
      else
	roa_maxprefixlen = roa_prefixlen;

      /*
       * If the prefix we're looking for is smaller than the smallest
       * allowed slice of the ROA prefix, the ROA can't possibly
       * cover.
       */
      if (prefixlen > roa_maxprefixlen)
	continue;

      /*
       * If we get this far, we have to compare prefixes.
       */
      assert(roa_prefixlen <= ADDR_RAW_BUF_LEN * 8);
      plen = prefixlen < roa_prefixlen ? prefixlen : roa_prefixlen;
      k = 0;
      while (plen >= 8 && prefix[k] == roa_prefix[k]) {
	plen -= 8;
	k++;
      }
      if (plen > 8 || ((prefix[k] ^ roa_prefix[k]) & (0xFF << (8 - plen))) != 0)
	continue;

      /*
       * If we get here, we have a match.
       */
      printf("ASN %lu prefix ", asid);
      switch (prefix_afi) {
      case IANA_AFI_IPV4:
	printf("%u.%u.%u.%u", prefix[0], prefix[1], prefix[2], prefix[3]);
	break;
      case IANA_AFI_IPV6:
	for (n = 16; n > 1 && prefix[n-1] == 0x00 && prefix[n-2] == 0x00; n -= 2)
	  ;
	for (k = 0; k < n; k += 2)
	  printf("%x%s", (prefix[k] << 8) | prefix[k+1], (k < 14 ? ":" : ""));
	if (k < 16)
	  printf(":");
	break;
      }
      printf("/%lu ROA %s\n", prefixlen, filename);
      goto done;
    }
  }

 done:
  BIO_free(b);
  CMS_ContentInfo_free(cms);
  ROA_free(r);
}

/*
 * Walk a directory tree
 */
static int handle_directory(const char *name, const unsigned prefix_afi, const unsigned char *prefix, const unsigned long prefixlen)
{
  char path[FILENAME_MAX];
  struct dirent *d;
  size_t len;
  DIR *dir;
  int ret = 0, need_slash;

  assert(name);
  len = strlen(name);
  assert(len > 0 && len < sizeof(path));
  need_slash = name[len - 1] != '/';

  if ((dir = opendir(name)) == NULL)
    lose_errno("Couldn't open directory", name);

  while ((d = readdir(dir)) != NULL) {
    if (!strcmp(d->d_name, ".") || !strcmp(d->d_name, ".."))
      continue;
    if (len + strlen(d->d_name) + need_slash >= sizeof(path))
      lose("Constructed path name too long", d->d_name);
    strcpy(path, name);
    if (need_slash)
      strcat(path, "/");
    strcat(path, d->d_name);
    switch (d->d_type) {
    case DT_DIR:
      if (!handle_directory(path, prefix_afi, prefix, prefixlen))
	lose("Directory walk failed", path);
      continue;
    default:
      if (has_suffix(path, ".roa"))
	file_handler(path, prefix_afi, prefix, prefixlen);
      continue;
    }
  }

  ret = 1;

 done:
  if (dir)
    closedir(dir);
  return ret;
}

static void usage (const char *jane, const int code)
{
  fprintf(code ? stderr : stdout, "usage: %s authtree prefix [prefix...]\n", jane);
  exit(code);
}

int main (int argc, char *argv[])
{
  unsigned char prefix[ADDR_RAW_BUF_LEN];
  unsigned long prefixlen;
  unsigned afi;
  char *s = NULL, *p = NULL;
  int i, len, ret = 1;

  if (argc == 2 && (strcmp(argv[1], "-h") || strcmp(argv[1], "--help")))
    usage(argv[0], 0);

  if (argc < 3)
    usage(argv[0], 1);

  OpenSSL_add_all_algorithms();
  ERR_load_crypto_strings();

  for (i = 2; i < argc; i++) {

    if ((s = strdup(argv[i])) == NULL)
      lose("Couldn't strdup()", argv[i]);

    if ((p = strchr(s, '/')) != NULL)
      *p++ = '\0';

    len = a2i_ipadd(prefix, s);

    switch (len) {
    case  4: afi = IANA_AFI_IPV4; break;
    case 16: afi = IANA_AFI_IPV6; break;
    default: lose("Unknown AFI", argv[i]);
    }

    if (p) {
      if (*p == '\0' ||
	  (prefixlen = strtoul(p, &p, 10)) == ULONG_MAX ||
	  *p != '\0' || 
	  prefixlen > ADDR_RAW_BUF_LEN * 8)
	lose("Bad prefix length", argv[i]);
    } else  {
      prefixlen = len * 8;
    }

    assert(prefixlen <= ADDR_RAW_BUF_LEN * 8);

    free(s);
    p = s = NULL;

    if (!handle_directory(argv[1], afi, prefix, prefixlen))
      goto done;

  }

  ret = 0;

 done:
  if (s)
    free(s);
  return ret;
}
