# /// script
# requires-python = ">=3.12,<3.14"
# dependencies = ["flask>=3.1", "markdown-it-py", "nh3", "websocket-client", "boto3", "cowsay==6.1", "wcwidth==0.2.13"]
# ///
"""Recover historical tags without inventing them or rewinding the cursor. -- PI/OpenAI"""
import json
import os
import tempfile

with tempfile.TemporaryDirectory() as directory:
    os.environ.update(SEARCH_DB_PATH=f'{directory}/search.db',GETLOG_DB=f'{directory}/getlog.db',NIP05_DB=f'{directory}/nip05.db')
    import search
    search.init_db()
    search.save_last_seen(9)
    search.save_last_seen(100)
    search.save_last_seen(20)
    assert search.get_last_seen()==100
    search.save_last_seen(5000)
    conn=search.get_db()
    for i,flat in enumerate(['131089 agent-intro alignment','131089','']):
        event={'id':str(i+1)*64,'pubkey':'a'*64,'kind':1,'created_at':1,'content':'Historical welcome','tags':[['nonce','131089','16'],['t','agent-intro'],['t','alignment']]}
        search.index_event(event)
        conn.execute('UPDATE events SET tags=? WHERE id=?',(flat,event['id']));conn.commit()
        filters=search.subscription_filters()
        assert filters[0]['since']==1400
        assert event['id'] in filters[1]['ids']
        search.index_event(event)
        search.save_last_seen(event['created_at'])
        assert search.get_last_seen()==5000
        assert len(search.subscription_filters())==1
        response=search.app.test_client().get('/p/'+event['id']+'?json')
        assert response.status_code==200 and response.json['tags']==event['tags']
        assert search.app.test_client().get('/p/'+event['id']).status_code==200
        search.index_event(event)
        assert json.loads(conn.execute('SELECT tags FROM events WHERE id=?',(event['id'],)).fetchone()[0])==event['tags']
    assert conn.execute('SELECT COUNT(*) FROM events').fetchone()[0]==3
    conn.execute("INSERT INTO event_search(event_search,rank) VALUES('integrity-check',1)")
    conn.commit();conn.close()
print('PASS: legacy flat/numeric/empty tags recovered from replay; HTML/JSON work, FTS stays consistent, old events cannot rewind numeric cursor')
