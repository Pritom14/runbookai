FROM ubuntu:22.04

RUN apt-get update && apt-get install -y openssh-server nginx stress-ng procps iproute2 dnsutils net-tools curl

RUN mkdir -p /run/sshd

RUN mkdir -p /root/.ssh
COPY demo/chaos/keys/authorized_keys /root/.ssh/authorized_keys
RUN chmod 700 /root/.ssh && chmod 600 /root/.ssh/authorized_keys && chown -R root:root /root/.ssh

RUN sed -i 's/#PermitRootLogin prohibit-password/PermitRootLogin yes/' /etc/ssh/sshd_config

EXPOSE 22

COPY demo/chaos/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

CMD ["/entrypoint.sh"]
