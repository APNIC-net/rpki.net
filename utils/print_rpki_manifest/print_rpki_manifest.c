/*
 * Copyright (C) 2008  American Registry for Internet Numbers ("ARIN")
 *
 * Permission to use, copy, modify, and distribute this software for any
 * purpose with or without fee is hereby granted, provided that the above
 * copyright notice and this permission notice appear in all copies.
 *
 * THE SOFTWARE IS PROVIDED "AS IS" AND ARIN DISCLAIMS ALL WARRANTIES WITH
 * REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY
 * AND FITNESS.  IN NO EVENT SHALL ARIN BE LIABLE FOR ANY SPECIAL, DIRECT,
 * INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM
 * LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE
 * OR OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR
 * PERFORMANCE OF THIS SOFTWARE.
 */

/* $Id$ */

/*
 * Decoder test for RPKI manifests.
 *
 * NB: This does -not- check the CMS signatures, just the encoding.
 */

#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>
#include <unistd.h>

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

#include <rpki/manifest.h>

/*
 * Read manifest (CMS object) in DER format.
 *
 * NB: When invoked this way, CMS_verify() does -not- verify, it just decodes the ASN.1.
 *
 * OK, this does more than just reading the CMS.  Refactor or rename, someday.
 */

static const Manifest *read_manifest(const char *filename,
				     const int print_cms,
				     const int print_manifest,
				     const int print_signerinfo)
{
  CMS_ContentInfo *cms = NULL;
  const ASN1_OBJECT *oid = NULL;
  const Manifest *m = NULL;
  char buf[512];
  BIO *b = NULL;
  int i, j;

  if ((b = BIO_new_file(filename, "r")) == NULL ||
      (cms = d2i_CMS_bio(b, NULL)) == NULL)
    goto done;
  BIO_free(b);
  b = NULL;

  if (print_signerinfo) {
    STACK_OF(CMS_SignerInfo) *signerInfos = CMS_get0_SignerInfos(cms);
    STACK_OF(X509) *certs = CMS_get1_certs(cms);
    STACK_OF(X509_CRL) *crls = CMS_get1_crls(cms);
    printf("Certificates:   %d\n", certs ? sk_X509_num(certs) : 0);
    printf("CRLs:           %d\n", crls ? sk_X509_CRL_num(crls) : 0);
    for (i = 0; i < sk_CMS_SignerInfo_num(signerInfos); i++) {
      CMS_SignerInfo *si = sk_CMS_SignerInfo_value(signerInfos, i);
      ASN1_OCTET_STRING *hash = NULL;
      printf("SignerId[%d]:    ", i);
      if (CMS_SignerInfo_get0_signer_id(si, &hash, NULL, NULL) && hash != NULL)
	for (j = 0; j < hash->length; j++)
	  printf("%02x%s", hash->data[j], j == hash->length - 1 ? "" : ":");
      else
	printf("[Could not read SID]");
      if (certs)
	for (j = 0; j < sk_X509_num(certs); j++)
	  if (!CMS_SignerInfo_cert_cmp(si, sk_X509_value(certs, j)))
	    printf(" [Matches certificate %d]", j);
      if ((j = CMS_signed_get_attr_by_NID(si, NID_pkcs9_signingTime, -1)) >= 0) {
	X509_ATTRIBUTE *xa = CMS_signed_get_attr(si, j);
	if (xa && !xa->single && sk_ASN1_TYPE_num(xa->value.set) == 1) {
	  ASN1_TYPE *so = sk_ASN1_TYPE_value(xa->value.set, 0);
	  switch (so->type) {
	  case V_ASN1_UTCTIME:
	    printf(" [signingTime(U) %s%s]",
		   so->value.utctime->data[0] < '5' ? "20" : "19",
		   so->value.utctime->data);
	    break;
	  case  V_ASN1_GENERALIZEDTIME:
	    printf(" [signingTime(G) %s]",
		   so->value.generalizedtime->data);
	    break;
	  }
	}
      }
      printf("\n");
    }
    sk_X509_pop_free(certs, X509_free);
    sk_X509_CRL_pop_free(crls, X509_CRL_free);
  }

  if ((b = BIO_new(BIO_s_mem())) == NULL ||
      CMS_verify(cms, NULL, NULL, NULL, b, CMS_NOCRL | CMS_NO_SIGNER_CERT_VERIFY | CMS_NO_ATTR_VERIFY | CMS_NO_CONTENT_VERIFY) <= 0 ||
      (m = ASN1_item_d2i_bio(ASN1_ITEM_rptr(Manifest), b, NULL)) == NULL)
    goto done;
  BIO_free(b);
  b = NULL;

  if (print_manifest) {

    if ((oid = CMS_get0_eContentType(cms)) == NULL)
      goto done;
    OBJ_obj2txt(buf, sizeof(buf), oid, 0);
    printf("eContentType:   %s\n", buf);

    if (m->version)
      printf("version:        %ld\n", ASN1_INTEGER_get(m->version));
    else
      printf("version:        0 [Defaulted]\n");
    printf("manifestNumber: %ld\n", ASN1_INTEGER_get(m->manifestNumber));
    printf("thisUpdate:     %s\n", m->thisUpdate->data);
    printf("nextUpdate:     %s\n", m->nextUpdate->data);
    OBJ_obj2txt(buf, sizeof(buf), m->fileHashAlg, 0);
    printf("fileHashAlg:    %s\n", buf);

    for (i = 0; i < sk_FileAndHash_num(m->fileList); i++) {
      FileAndHash *fah = sk_FileAndHash_value(m->fileList, i);
      printf("fileList[%3d]:  ", i);
      for (j = 0; j < fah->hash->length; j++)
	printf("%02x%s", fah->hash->data[j], j == fah->hash->length - 1 ? " " : ":");
      printf(" %s\n", fah->file->data);
    }

    if (X509_cmp_current_time(m->nextUpdate) < 0)
      printf("MANIFEST IS STALE\n");
  }

  if (print_cms) {
    if (print_manifest)
      printf("\n");
    fflush(stdout);
    if ((b = BIO_new(BIO_s_fd())) == NULL)
      goto done;
    BIO_set_fd(b, 1, BIO_NOCLOSE);
    CMS_ContentInfo_print_ctx(b, cms, 0, NULL);
    BIO_free(b);
    b = NULL;
  }

 done:
  if (ERR_peek_error())
    ERR_print_errors_fp(stderr);
  BIO_free(b);
  CMS_ContentInfo_free(cms);
  return m;
}

/*
 * Main program.
 */
int main (int argc, char *argv[])
{
  int result = 0, print_cms = 0, c;
  char *jane = argv[0];

  OpenSSL_add_all_algorithms();
  ERR_load_crypto_strings();

  while ((c = getopt(argc, argv, "c")) != -1) {
    switch (c) {
    case 'c':
      print_cms = 1;
      break;
    case '?':
    default:
      fprintf(stderr, "usage: %s [-c] manifest [manifest...]\n", jane);
      return 1;
    }
  }

  argc -= optind;
  argv += optind;

  while (argc-- > 0)
    result |=  read_manifest(*argv++, print_cms, 1, 1) == NULL;
  return result;
}
