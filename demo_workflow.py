"""Run the main use cases through an actual temporary HTTP server and SQLite DB."""
import json
import http.client
import tempfile
import threading
from pathlib import Path
from src.bootstrap import build_application
from src.domain.models import Actor
from src.presentation.http_api import make_server


def main():
    with tempfile.TemporaryDirectory() as temp:
        root=Path(temp)
        app=build_application(root/'demo.db',root/'mailbox.jsonl')
        app.auth.create_user(Actor('local-bootstrap','admin'),'admin','DemoPass123!','admin')
        server=make_server(app,port=0)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        token=''
        def call(method,path,data=None):
            connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
            body=json.dumps(data).encode() if data is not None else None
            connection.request(method,path,body,{'Content-Type':'application/json','Authorization':'Bearer '+token})
            response=connection.getresponse(); raw=response.read(); connection.close()
            parsed=json.loads(raw) if 'application/json' in response.getheader('Content-Type','') else raw.decode('utf-8-sig')
            if response.status>=400:
                raise RuntimeError(f'{method} {path}: {response.status} {parsed}')
            value=parsed['data'] if isinstance(parsed,dict) else parsed
            print(json.dumps({'method':method,'path':path,'http_status':response.status,'result':value if 'login' not in path else {'user':value['user'],'token':'<redacted>'}},ensure_ascii=False))
            return value
        try:
            login=call('POST','/api/auth/login',{'username':'admin','password':'DemoPass123!'})
            token=login['access_token']
            payload=dict(tracking_no='SF-DEMO-001',recipient='resident-001',phone='13800000000',carrier='测试快递',location='A-01',request_key='demo-inbound-001')
            first=call('POST','/api/parcels',payload)
            replay=call('POST','/api/parcels',payload)
            assert first['id']==replay['id']
            call('GET','/api/parcels?status=stored')
            call('POST','/api/notifications/dispatch',{'limit':20})
            code=json.loads((root/'mailbox.jsonl').read_text(encoding='utf-8'))['pickup_code']
            call('POST',f'/api/parcels/{first["id"]}/pickup',{'recipient':'resident-001','pickup_code':code})
            second=call('POST','/api/parcels',{**payload,'tracking_no':'SF-DEMO-002','request_key':'demo-inbound-002'})
            event=call('POST',f'/api/parcels/{second["id"]}/exceptions',{'kind':'damaged','note':'包装破损，待核实'})
            call('POST',f'/api/exceptions/{event["id"]}/resolve',{'resolution':'return','note':'已交回承运商'})
            call('GET','/api/statistics')
            call('GET','/api/parcels/export.csv')
            call('GET','/api/audits?parcel_id=1')
            print('DEMO PASSED: login -> receive/retry -> query -> local notification -> pickup -> exception/return -> statistics/export/audit')
        finally:
            server.shutdown();server.server_close();thread.join(timeout=2)


if __name__=='__main__':
    main()
