#!/bin/bash

RELEASE_ARCH=$(lsb_release -cs)-$(dpkg --print-architecture)
case $RELEASE_ARCH in
    "bullseye-amd64") WKHTMLTOPDF_SOURCE="https://github.com/wkhtmltopdf/packaging/releases/download/0.12.6.1-2/wkhtmltox_0.12.6.1-2.bullseye_amd64.deb" ;;
    "bullseye-arm64") WKHTMLTOPDF_SOURCE="https://github.com/wkhtmltopdf/packaging/releases/download/0.12.6.1-2/wkhtmltox_0.12.6.1-2.bullseye_arm64.deb" ;;
    "buster-amd64") WKHTMLTOPDF_SOURCE="https://github.com/wkhtmltopdf/packaging/releases/download/0.12.6-1/wkhtmltox_0.12.6-1.buster_amd64.deb" ;;
    "buster-arm64") WKHTMLTOPDF_SOURCE="https://github.com/wkhtmltopdf/packaging/releases/download/0.12.6-1/wkhtmltox_0.12.6-1.buster_arm64.deb" ;;

    *) echo "Cannot determine WKHTMLtoPDF version to install" && exit 1 ;;
esac

echo "Getting WKHTMLtoPDF from: $WKHTMLTOPDF_SOURCE"
curl -o wkhtmltox.deb -sSL ${WKHTMLTOPDF_SOURCE}
apt-get install -y --no-install-recommends ./wkhtmltox.deb
rm -rf wkhtmltox.deb
