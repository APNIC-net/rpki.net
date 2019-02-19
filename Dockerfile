FROM ubuntu:16.04
USER root
RUN apt-get update && \
    apt-get install -y \
        wget \
        xz-utils \
        gcc \
        python \
        rrdtool \
        python-dev \
        rsync \
	postgresql \
	postgresql-contrib \
        python-lxml \
        python-django \
        python-vobject \
        python-yaml \
        xsltproc \
        python-django-south \
        make \
        strace \
        rsync \
        python-tornado \
        git \
        vim \
        bsdmainutils
RUN wget -q -O /etc/apt/trusted.gpg.d/rpki.gpg https://download.rpki.net/APTng/apt-gpg-key.gpg
RUN wget -q -O /etc/apt/sources.list.d/rpki.list https://download.rpki.net/APTng/rpki.xenial.list
RUN apt-get update && apt-get install -d -y rpki-ca
COPY docker/rsyncd.conf /etc/rsyncd.conf
COPY rpki/irdb/zookeeper.py /
COPY rpki/relaxng.py /
COPY rpki/rpkic.py /
COPY rpki/rpkidb/models.py /
COPY rpki/issue-ee /
RUN mkdir /rpki
COPY docker/start /rpki

CMD ["/rpki/start"]
