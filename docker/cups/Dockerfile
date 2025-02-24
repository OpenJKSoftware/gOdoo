FROM ubuntu:24.04

# ENV variables
ENV DEBIAN_FRONTEND="noninteractive" \
    TZ="Europe/Berlin" \
    CUPSADMIN="admin" \
    CUPSPASSWORD="admin"

# Install dependencies
RUN set -ex; \
    apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
    apt-utils \
    usbutils \
    cups \
    cups-filters \
    libcups2-dev \
    cups-pdf \
    inotify-tools \
    avahi-daemon \
    avahi-utils \
    libnss-mdns \
    tzdata \
    printer-driver-all \
    printer-driver-cups-pdf \
    printer-driver-foo2zjs \
    ghostscript \
    libcupsimage2 \
    foomatic-db-compressed-ppds \
    openprinting-ppds \
    hpijs-ppds \
    hp-ppd \
    hplip \
    samba-client

RUN set -ex; \
    echo '*' > /etc/mdns.allow \
    && sed -i "s/hosts:.*/hosts:          files mdns4 dns/g" /etc/nsswitch.conf

COPY cups_entrypoint.sh /
RUN chmod +x /cups_entrypoint.sh

COPY config /etc/cups

CMD ["/cups_entrypoint.sh"]
