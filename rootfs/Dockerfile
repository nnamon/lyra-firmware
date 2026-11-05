# Tools for unpacking a Lyra rootfs.img. UBI is a raw NAND volume format with
# no loop mount, so these tools read and write the image directly.
FROM debian:bookworm

RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      mtd-utils python3 python3-pip file \
 && rm -rf /var/lib/apt/lists/*

# ubifs.sh uses the -k and -x flags as they behave in 0.8.16.
RUN pip3 install --break-system-packages --no-cache-dir ubi_reader==0.8.16

WORKDIR /rootfs
