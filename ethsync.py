# Indexer for Ethereum to get transaction list by ETH address
# https://github.com/Adamant-im/ETH-transactions-storage

# Contributors:
# v2.4.2
# 2024- @maxysoft (maxysoft@maxynetwork.it)
# 2022-2024 ADAMANT Foundation (devs@adamant.im), @twhitehead00, Tyvan Cheng (tyvancheng@gmail.com)
# 2021-2022 ADAMANT Foundation (devs@adamant.im), Francesco Bonanno (mibofra@parrotsec.org),
# Guénolé de Cadoudal (guenoledc@yahoo.fr), Drew Wells (drew.wells00@gmail.com)
# 2020-2021 ADAMANT Foundation (devs@adamant.im): Aleksei Lebedev
# 2017-2020 ADAMANT TECH LABS LP (pr@adamant.im): Artem Brunov, Aleksei Lebedev

import os
from web3 import Web3
from web3.exceptions import TimeExhausted
from web3.middleware import geth_poa_middleware
import psycopg2
import time
import sys
import logging

# Get env variables or set to default
dbuser = os.getenv("DB_USER")
dbpassword = os.getenv("DB_PASSWORD")
databasename = os.getenv("DATABASE_NAME")
dbhost = os.getenv("DB_HOST")
startBlock = os.getenv("START_BLOCK") or "1"
confirmationBlocks = os.getenv("CONFIRMATIONS_BLOCK") or "0"
nodeUrl = os.getenv("ETH_URL")
pollingPeriod = os.getenv("PERIOD") or "20"
logFile = os.getenv("LOG_FILE")

if databasename is None:
    print('Add postgre database in env var DATABASE_NAME')
    exit(2)

if nodeUrl is None:
    print('Add eth url in env var ETH_URL')
    exit(2)

# Connect to Ethereum node
if nodeUrl.startswith("http"):
    web3 = Web3(Web3.HTTPProvider(nodeUrl)) # "http://publicnode:8545"
elif nodeUrl.startswith("ws"):
    web3 = Web3(Web3.WebsocketProvider(nodeUrl, websocket_timeout=60, websocket_kwargs={'max_size': 10**7})) # "ws://publicnode:8546"
else:
    web3 = Web3(Web3.IPCProvider(nodeUrl)) # "/home/geth/.ethereum/geth.ipc"

web3.middleware_onion.inject(geth_poa_middleware, layer=0)

# Start logger
logger = logging.getLogger("eth-sync")
logger.setLevel(logging.INFO)

# File logger
if logFile is None:
    lfh = logging.StreamHandler()
else:
    lfh = logging.FileHandler(logFile)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
lfh.setFormatter(formatter)
logger.addHandler(lfh)

# Systemd logger, if we want to user journalctl logs
# Install systemd-python and
# decomment "#from systemd.journal import JournalHandler" up
#ljc = JournalHandler()
#formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
#ljc.setFormatter(formatter)
#logger.addHandler(ljc)

try:
    logger.info("Trying to connect to " + databasename + " database…")
    conn_string = f"dbname={databasename} user={dbuser} password={dbpassword} host={dbhost}"
    conn = psycopg2.connect(conn_string)
    conn.autocommit = True
    logger.info("Connected to the database")
except Exception as e:
    logger.error(f"Unable to connect to database: {e}")
    exit(1)

# Delete last block as it may be not imported in full
cur = conn.cursor()
cur.execute('DELETE FROM public.ethtxs WHERE block = (SELECT Max(block) from public.ethtxs)')
cur.close()
conn.close()

# Wait for the node to be in sync before indexing
while web3.eth.syncing != False:
    # Change with the time, in second, do you want to wait
    # before checking again, default is 5 minutes
    logger.info("Waiting Ethereum node to be in sync…")
    time.sleep(300)

logger.info("Ethereum node is synced.")

# Adds all transactions from Ethereum block
def insertTxsFromBlock(block):
    blockid = block['number']
    time = block['timestamp']
    for txNumber in range(0, len(block.transactions)):
        trans = block.transactions[txNumber]
        transReceipt = web3.eth.get_transaction_receipt(trans['hash'])
        # Save also transaction status, should be null if pre byzantium blocks
        # status = bool(transReceipt['status'])
        txhash = trans['hash'].hex()
        value = trans['value']
        inputinfo = trans['input']
        # Check if transaction is a contract transfer
        if (value == 0 and not inputinfo.hex().startswith('0xa9059cbb')):
            continue
        fr = trans['from']
        to = trans['to']
        gasprice = trans['gasPrice']
        gas = transReceipt['gasUsed']
        contract_to = ''
        contract_value = ''
        # Check if transaction is a contract transfer
        if inputinfo.hex().startswith('0xa9059cbb'):
            contract_to = inputinfo.hex()[10:-64]
            contract_value = inputinfo.hex()[74:]
        # Correct contract transfer transaction represents '0x' + 4 bytes 'a9059cbb' + 32 bytes (64 chars) for contract address and 32 bytes for its value
        # Some buggy txs can break up Indexer, so we'll filter it
        if len(contract_to) > 128:
            logger.info('Skipping ' + str(txhash) + ' tx. Incorrect contract_to length: ' + str(len(contract_to)))
            contract_to = ''
            contract_value = ''
        cur.execute(
            'INSERT INTO public.ethtxs(time, txfrom, txto, value, gas, gasprice, block, txhash, contract_to, contract_value) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
            (time, fr, to, value, gas, gasprice, blockid, txhash, contract_to, contract_value))

# Fetch all of new (not in index) Ethereum blocks and add transactions to index
def get_block_with_retry(blockHeight, retries=3, delay=5):
    for i in range(retries):
        try:
            return web3.eth.get_block(blockHeight, True)
        except TimeExhausted:
            if i < retries - 1:
                time.sleep(delay)
            else:
                raise

while True:
    try:
        logger.info("Trying to connect to " + databasename + " database…")
        conn_string = f"dbname={databasename} user={dbuser} password={dbpassword} host={dbhost}"
        conn = psycopg2.connect(conn_string)
        conn.autocommit = True
    except Exception as e:
        logger.error(f"Unable to connect to database: {e}")

    cur = conn.cursor()

    cur.execute('SELECT Max(block) from public.ethtxs')
    maxblockindb = cur.fetchone()[0]
    if maxblockindb is None:
        maxblockindb = int(startBlock)

    endblock = int(web3.eth.block_number) - int(confirmationBlocks)

    logger.info('Current best block in index: ' + str(maxblockindb) + '; in Ethereum chain: ' + str(endblock))

    for blockHeight in range(maxblockindb + 1, endblock):
        block = get_block_with_retry(blockHeight)
        if len(block.transactions) > 0:
            insertTxsFromBlock(block)
            logger.info('Block ' + str(blockHeight) + ' with ' + str(len(block.transactions)) + ' transactions is processed')
        else:
            logger.info('Block ' + str(blockHeight) + ' does not contain transactions')
    cur.close()
    conn.close()
    time.sleep(int(pollingPeriod))
