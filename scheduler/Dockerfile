FROM alpine:3.19

RUN apk add --no-cache bash curl python3 py3-pip

# Install requests for health checks
RUN pip3 install requests

COPY cron/rag-updater /etc/cron.d/rag-updater
RUN chmod 0644 /etc/cron.d/rag-updater

# Script di aggiornamento
COPY scripts/update.sh /usr/local/bin/update.sh
RUN chmod +x /usr/local/bin/update.sh

CMD ["crond", "-f", "-l", "2"]