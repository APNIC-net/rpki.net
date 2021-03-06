# RPKI Relying Party Tools

These tools implements the "relying party" role of the RPKI system, that is,
the entity which retrieves RPKI objects from repositories, validates them, and
uses the result of that validation process as input to other processes, such
as BGP security.

See the [CA tools][1] for programs to help you generate RPKI objects, if you
need to do that.

The RP main tools are [rcynic][2] and [rpki-rtr][3], each of which is
discussed below.

The installation process sets up everything you need for a basic RPKI
validation installation. You will, however, need to think at least briefly
about which [RPKI trust anchors][4] you are using, and may need to change
these from the defaults.

The installation process sets up a cron job running running [rcynic-cron][5]
as user "`rcynic`" once per hour at a randomly-selected minute.

## rcynic

rcynic is the primary validation tool. It does the actual work of RPKI
validation: checking syntax, signatures, expiration times, and conformance to
the profiles for RPKI objects. The other relying party programs take rcynic's
output as their input.

The installation process sets up a basic rcynic configuration. See the [rcynic
documentation][6] if you need to know more.

See the [discussion of trust anchors][4].

## rpki-rtr

rpki-rtr is an implementation of the rpki-rtr protocol, using rcynic's output
as its data source. rpki-rtr includes the rpki-rtr server, a test client, and
a utiltity for examining the content of the database rpki-rtr generates from
the data supplied by rcynic.

See the [rpki-rtr documentation][7] for further details.

## rcynic-cron

rcynic-cron is a small script to run the most common set of relying party
tools under cron. See the [discussion of running relying party tools under
cron][8] for further details.

## Selecting trust anchors

As in any PKI system, validation in the RPKI system requires a set of "trust
anchors" to use as a starting point when checking certificate chains. By
definition, trust anchors can only be selected by you, the relying party.

As with most other PKI software, we supply a default set of trust anchors
which you are welcome to use if they suit your needs. These are installed as
part of the normal installation process, so if you don't do anything, you'll
get these. You can, however, override this if you need something different;
see [the rcynic documentation][6] for details.

Remember: It's only a trust anchor if **you** trust it. We can't make that
decision for you.

Also note that, at least for now, ARIN's trust anchor locator is absent from
the default set of trust anchors. This is not an accident: it's the direct
result of a deliberate policy decision by ARIN to require anyone using their
trust anchor to [jump through legal hoops][9]. If you have a problem with
this, [complain to ARIN][10]. If and when ARIN changes this policy, we will be
happy to include their trust anchor locator along with those of the other
RIRs.

   [1]: #_.wiki.doc.RPKI.CA

   [2]: #_.wiki.doc.RPKI.RP#rcynic

   [3]: #_.wiki.doc.RPKI.RP#rpki-rtr

   [4]: #_.wiki.doc.RPKI.RP#trust-anchors

   [5]: #_.wiki.doc.RPKI.RP#rcynic-cron

   [6]: #_.wiki.doc.RPKI.RP.rcynic

   [7]: #_.wiki.doc.RPKI.RP.rpki-rtr

   [8]: #_.wiki.doc.RPKI.RP.RunningUnderCron

   [9]: https://www.arin.net/resources/rpki/faq.html#tal

   [10]: http://lists.arin.net/mailman/listinfo/arin-ppml

