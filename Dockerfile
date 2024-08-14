FROM python:3.7-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt update -y && apt install -y build-essential web3 psycopg2-binary
RUN apt autoremove -y && apt clean && rm -rf /var/lib/apt/lists/*

RUN mkdir /eth-storage

COPY ./ethsync.py /eth-storage

WORKDIR /eth-storage
ENTRYPOINT [ "python3.7", "./ethsync.py" ]
