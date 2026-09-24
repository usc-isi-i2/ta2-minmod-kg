FROM python:3.12-slim

# install required system libraries
RUN apt update && apt install -y ca-certificates build-essential curl git git-lfs lz4 && \
    install -m 0755 -d /etc/apt/keyrings && \
    curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc && \
    chmod a+r /etc/apt/keyrings/docker.asc && \
    echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian \
    $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
    tee /etc/apt/sources.list.d/docker.list > /dev/null && \
    apt update && \
    apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin 

ARG UID=1000
ARG GID=1000

RUN groupadd -f -g $GID criticalmaas && useradd -ms /bin/bash criticalmaas -u $UID -g $GID
# add user to docker group
RUN usermod -aG docker criticalmaas

USER criticalmaas

ENV PATH="/home/criticalmaas/.local/bin:${PATH}"

# update Git
RUN git config --global --add safe.directory "*" && \
    git config --global user.email "minmod@isi.edu" && \
    git config --global user.name "MinMod"

# install libraries
RUN mkdir -p /home/criticalmaas/kg/minmodkg && \
    touch /home/criticalmaas/kg/minmodkg/__init__.py
ADD pyproject.toml /home/criticalmaas/kg/
ADD poetry.lock /home/criticalmaas/kg/
ADD README.md /home/criticalmaas/kg/

RUN cd /home/criticalmaas/kg && pip install .

ADD --chown=criticalmaas:criticalmaas minmodkg /home/criticalmaas/kg/minmodkg
ADD --chown=criticalmaas:criticalmaas extractors /home/criticalmaas/kg/extractors
ADD --chown=criticalmaas:criticalmaas schema /home/criticalmaas/kg/schema
ADD --chown=criticalmaas:criticalmaas vendor/ta2-table-understanding/schema /home/criticalmaas/kg/vendor/ta2-table-understanding/schema

RUN cd /home/criticalmaas/kg && pip install --no-deps .