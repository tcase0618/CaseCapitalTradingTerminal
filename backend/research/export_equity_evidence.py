"""Read-only SSH/PostgreSQL evidence export. Never imports terminal services."""
import argparse
import getpass
import json
from pathlib import Path
import sys

import paramiko

REMOTE = r'''
import asyncio, json, os
from pathlib import Path
from dotenv import load_dotenv
import asyncpg
load_dotenv('/opt/case-capital/stock-intel/backend/.env')
load_dotenv('/opt/case-capital/stock-intel/.env')
async def run():
    if MODE == 'bars':
        import sys, logging
        sys.path.insert(0,'/opt/case-capital/stock-intel/backend')
        from services.public_api import PublicAPIClient
        logging.disable(logging.CRITICAL)
        async with PublicAPIClient() as client:
            queue=asyncio.Queue()
            for symbol in COLLECTIONS: queue.put_nowait(symbol)
            pacing=asyncio.Lock()
            async def worker():
                while not queue.empty():
                    try: symbol=queue.get_nowait()
                    except asyncio.QueueEmpty: return
                    for attempt in range(3):
                        async with pacing: await asyncio.sleep(0.6)
                        try:
                            payload=await asyncio.wait_for(client.bars(symbol,days=365),timeout=30)
                            print(json.dumps({'symbol':symbol,'ok':True,'payload':payload},default=str),flush=True)
                            break
                        except Exception as exc:
                            if attempt==2: print(json.dumps({'symbol':symbol,'ok':False,'error_class':type(exc).__name__}),flush=True)
                            else: await asyncio.sleep(2**(attempt+1))
            await asyncio.gather(*(worker() for _ in range(4)))
        return
    conn = await asyncpg.connect(os.environ['POSTGRES_DSN'], command_timeout=120)
    try:
        async with conn.transaction(isolation='repeatable_read', readonly=True):
            await conn.execute("set local statement_timeout='120s'")
            if MODE == 'inventory':
                rows = await conn.fetch('select collection,count(*) as n,min(updated_at) as oldest_update,max(updated_at) as newest_update from cc_collection_snapshots group by collection order by collection')
                print(json.dumps({'kind':'inventory','collections':[dict(r) for r in rows]},default=str))
            else:
                names = COLLECTIONS
                async for r in conn.cursor('select collection,doc_key,payload,updated_at from cc_collection_snapshots where collection=any($1::text[]) order by collection,doc_key', names, prefetch=64):
                    p = json.loads(r['payload']) if isinstance(r['payload'],str) else r['payload']
                    print(json.dumps({'collection':r['collection'],'doc_key':r['doc_key'],'payload':p,'updated_at':r['updated_at']},default=str),flush=True)
    finally: await conn.close()
asyncio.run(run())
'''

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['inventory','export','bars'], required=True)
    parser.add_argument('--collections', default='')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    password = getpass.getpass('VPS SSH password: ')
    client = paramiko.SSHClient()
    client.load_host_keys(str(Path.home()/'.ssh/known_hosts'))
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    client.connect('129.121.101.96',username='root',password=password,timeout=20,look_for_keys=False,allow_agent=False)
    del password
    try:
        # Locate the running interpreter, without sourcing or printing credentials.
        _, stdout, stderr = client.exec_command("systemctl show case-capital-terminal -p ExecStart --value",timeout=20)
        command = stdout.read().decode()
        candidates = ['/opt/case-capital/stock-intel/venv/bin/python','/opt/case-capital/stock-intel/.venv/bin/python','/opt/case-capital/stock-intel/backend/venv/bin/python']
        import re
        found = re.search(r'path=([^ ;]+)',command)
        if found:
            path = found.group(1)
            if path.endswith('/uvicorn'): candidates.insert(0,path.rsplit('/',1)[0]+'/python')
            elif 'python' in path: candidates.insert(0,path)
        interpreter = None
        for candidate in dict.fromkeys(candidates):
            _, out, _ = client.exec_command('test -x '+candidate+' && echo available',timeout=10)
            if out.read().strip() == b'available': interpreter = candidate; break
        if not interpreter: raise RuntimeError('Running Python interpreter not found')
        script = 'MODE='+repr(args.mode)+'\nCOLLECTIONS='+repr([x for x in args.collections.split(',') if x])+'\n'+REMOTE
        stdin, stdout, stderr = client.exec_command(interpreter+' -',timeout=3600 if args.mode == 'bars' else 150)
        stdin.write(script); stdin.flush(); stdin.channel.shutdown_write()
        target = Path(args.output); target.parent.mkdir(parents=True,exist_ok=True)
        count = 0
        with target.open('w',encoding='utf-8') as stream:
            for line in stdout:
                json.loads(line)
                stream.write(line); count += 1
        status = stdout.channel.recv_exit_status()
        if status:
            # Remote errors can contain DSNs; only expose the class of failure.
            raise RuntimeError('Read-only remote export failed; raw error withheld to protect configuration')
        print(json.dumps({'mode':args.mode,'rows':count,'output':str(target),'readonly':True}))
    finally: client.close()

if __name__ == '__main__': main()
