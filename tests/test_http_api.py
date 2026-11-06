"""End-to-end HTTP tests over a real loopback socket, no mocked HTTP server."""
import csv
import io
import json
import http.client
import tempfile
import threading
import unittest
from pathlib import Path
from src.bootstrap import build_application
from src.domain.models import Actor
from src.presentation.http_api import make_server


class HttpApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = build_application(self.root/'http.db',self.root/'mailbox.jsonl')
        self.app.auth.create_user(Actor('setup','admin'),'admin','Password123','admin')
        self.server = make_server(self.app,port=0)
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.token = self.app.auth.login('admin','Password123')['access_token']

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, method, path, data=None, token=None, raw=None, content_type='application/json'):
        connection = http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=5)
        headers = {'Content-Type':content_type,'Authorization':'Bearer '+(self.token if token is None else token)}
        body = raw if raw is not None else json.dumps(data).encode() if data is not None else None
        connection.request(method,path,body,headers)
        response = connection.getresponse(); content=response.read()
        result = json.loads(content) if 'application/json' in response.getheader('Content-Type','') else content.decode('utf-8-sig')
        status = response.status; connection.close()
        return status,result

    def parcel(self, **extra):
        data=dict(tracking_no='EXP001',recipient='resident-001',phone='13800000000',location='A-01',request_key='k1')
        data.update(extra)
        status,result=self.request('POST','/api/parcels',data)
        self.assertEqual(status,201,result)
        return result['data']

    def test_real_http_login_and_receive_to_pickup_flow(self):
        self.assertEqual(self.request('GET','/api/health',token='')[0],200)
        status,login=self.request('POST','/api/auth/login',{'username':'admin','password':'Password123'},token='')
        self.assertEqual(status,200)
        self.assertEqual(login['data']['token_type'],'Bearer')
        p=self.parcel()
        status,result=self.request('GET','/api/parcels?status=stored&page=1&page_size=10')
        self.assertEqual((status,result['data']['total']),(200,1))
        self.assertEqual(self.request('PATCH',f'/api/parcels/{p["id"]}/location',{'location':'B-02'})[1]['data']['location'],'B-02')
        self.assertEqual(self.request('GET',f'/api/parcels/{p["id"]}')[1]['data']['location'],'B-02')
        self.assertEqual(self.request('POST','/api/notifications/dispatch',{'limit':10})[1]['data']['sent'],1)
        code=json.loads((self.root/'mailbox.jsonl').read_text())['pickup_code']
        status,result=self.request('POST',f'/api/parcels/{p["id"]}/pickup',{'recipient':'resident-001','pickup_code':code})
        self.assertEqual((status,result['data']['status']),(200,'picked_up'))
        self.assertEqual(self.request('GET','/api/statistics')[1]['data']['by_status']['picked_up'],1)
        status,report=self.request('GET','/api/audits?parcel_id=1')
        self.assertEqual((status,report['data']['total']),(200,3))

    def test_real_http_exception_return_flow(self):
        p=self.parcel()
        status,result=self.request('POST',f'/api/parcels/{p["id"]}/exceptions',{'kind':'refused','note':'收件人拒收'})
        self.assertEqual(status,201)
        event_id=result['data']['id']
        status,result=self.request('POST',f'/api/exceptions/{event_id}/resolve',{'resolution':'return','note':'交回承运商'})
        self.assertEqual((status,result['data']['status']),(200,'returned'))

    def test_http_authentication_and_authorization(self):
        self.assertEqual(self.request('GET','/api/parcels',token='')[0],401)
        self.assertEqual(self.request('GET','/api/parcels',token='unknown')[0],401)
        self.request('POST','/api/users',{'username':'clerk','password':'ClerkPass123','role':'clerk'})
        token=self.request('POST','/api/auth/login',{'username':'clerk','password':'ClerkPass123'})[1]['data']['access_token']
        for path in ('/api/audits','/api/notifications','/api/parcels/export.csv'):
            self.assertEqual(self.request('GET',path,token=token)[0],403)
        self.assertEqual(self.request('POST','/api/users',{'username':'x','password':'Password123','role':'admin'},token=token)[0],403)

    def test_operator_is_derived_from_authenticated_session(self):
        self.parcel(operator='forged-user')
        event=self.request('GET','/api/audits')[1]['data']['items'][0]
        self.assertEqual(event['actor'],'admin')

    def test_not_found_and_duplicate_waybill_use_distinct_statuses(self):
        self.assertEqual(self.request('GET','/api/parcels/999')[0],404)
        self.assertEqual(self.request('GET','/api/not-a-route')[0],404)
        self.parcel()
        status,result=self.request('POST','/api/parcels',dict(tracking_no='EXP001',recipient='resident-001',phone='13800000000',location='A-01',request_key='new-key'))
        self.assertEqual((status,result['error']['code']),(409,'CONFLICT'))

    def test_malformed_json_unicode_and_body_types(self):
        for raw in (b'{',b'[]',b'null',b'NaN',b'{"limit":NaN}',b'\xff'):
            self.assertEqual(self.request('POST','/api/notifications/dispatch',raw=raw)[0],400)
        self.assertEqual(self.request('POST','/api/parcels',raw=b'{}',content_type='text/plain')[0],400)

    def test_query_validation_and_page_limits(self):
        for query in ('page=0','page_size=101','page=1&page=2','bad=1','status=unknown'):
            self.assertEqual(self.request('GET','/api/parcels?'+query)[0],400)

    def test_request_body_limit(self):
        self.assertEqual(self.request('POST','/api/parcels',raw=b'x'*65537)[0],413)

    def test_csv_reuses_shared_module_omits_identity_and_escapes_formulas(self):
        self.parcel(tracking_no='=1+1',location='@bad')
        status,content=self.request('GET','/api/parcels/export.csv')
        self.assertEqual(status,200)
        rows=list(csv.DictReader(io.StringIO(content)))
        self.assertEqual(rows[0]['tracking_no'],"'=1+1")
        self.assertEqual(rows[0]['location'],"'@bad")
        self.assertNotIn('recipient',rows[0])
        self.assertNotIn('phone',rows[0])

    def test_outbox_listing_redacts_phone_and_pickup_code(self):
        self.parcel()
        status,result=self.request('GET','/api/notifications')
        self.assertEqual(status,200)
        self.assertEqual(result['data']['items'][0]['status'],'pending')
        self.assertNotIn('13800000000',json.dumps(result))
        self.assertNotIn('pickup_code',json.dumps(result))
        self.assertEqual(self.request('GET','/api/parcels/overdue?days=3')[1]['data']['total'],0)

    def test_internal_exception_rolls_back_and_does_not_leak_storage_details(self):
        self.app.queue.before_commit=lambda: (_ for _ in ()).throw(RuntimeError('secret SQL detail'))
        data=dict(tracking_no='EXP001',recipient='resident-001',phone='13800000000',location='A-01',request_key='k1')
        with self.assertLogs(level='ERROR'):
            status,result=self.request('POST','/api/parcels',data)
        self.assertEqual(status,500)
        self.assertNotIn('secret SQL detail',json.dumps(result))
        self.app.queue.before_commit=None
        self.assertEqual(self.request('GET','/api/parcels')[1]['data']['total'],0)

    def test_create_user_validation_and_duplicate(self):
        payload={'username':'clerk','password':'ClerkPass123','role':'clerk'}
        self.assertEqual(self.request('POST','/api/users',payload)[0],201)
        self.assertEqual(self.request('POST','/api/users',payload)[0],409)
        self.assertEqual(self.request('POST','/api/users',{**payload,'username':'other','role':[]})[0],400)

    def test_http_pickup_reset_permission_and_audit(self):
        p=self.parcel()
        self.assertEqual(self.request('POST',f'/api/parcels/{p["id"]}/reset-pickup',{})[0],200)
        self.assertEqual(self.request('GET','/api/audits')[1]['data']['items'][0]['action'],'reset_pickup')


if __name__=='__main__':
    unittest.main()
