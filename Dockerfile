FROM ubuntu:22.04

RUN apt-get update && apt-get install -y openssh-server nginx stress-ng procps iproute2 dnsutils net-tools

RUN mkdir -p /run/sshd

COPY demo/chaos/keys/authorized_keys /run/sshd/authorized_keys
RUN chmod 600 /run/sshd/authorized_keys
RUN chown root:root /run/sshd/authorized_keys

RUN sed -i 's/#PermitRootLogin prohibit-password/PermitRootLogin yes/' /etc/ssh/sshd_config

EXPOSE 22

COPY demo/chaos/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

CMD ["/entrypoint.sh"]
