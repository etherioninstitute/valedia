"""Strict prerequisite for the actual owned native graph; importing starts nothing."""
import hashlib
import json
import math
import time
from mirofish_native_gateway import NativeRefused

EXPECTED_SCHEMA_SHA = 'a19b0e3a191307398296f31f5f605a904bcc5905af0069679ee57d6161071143'
EXPECTED_DDL_SHA = '6229f5897f2952af293be8b16e0ef800677b2922191846868d9264bb2b294828'
CONSTRAINTS = {'graph_uuid':('Graph','graph_id'),'entity_uuid':('Entity','uuid'),'episode_uuid':('Episode','uuid')}
INDEXES = {'entity_embedding':('VECTOR','NODE','Entity',('embedding',)),
           'fact_embedding':('VECTOR','RELATIONSHIP','RELATION',('fact_embedding',)),
           'entity_fulltext':('FULLTEXT','NODE','Entity',('name','summary')),
           'fact_fulltext':('FULLTEXT','RELATIONSHIP','RELATION',('fact','name'))}
SHOW_CONSTRAINTS='SHOW CONSTRAINTS YIELD name, type, entityType, labelsOrTypes, properties RETURN name, type, entityType, labelsOrTypes, properties LIMIT 128'
SHOW_INDEXES='SHOW INDEXES YIELD name, state, type, entityType, labelsOrTypes, properties, options RETURN name, state, type, entityType, labelsOrTypes, properties, options LIMIT 128'

class GraphGateRefused(NativeRefused):
    def __init__(self,message,proof=None,pending=False):
        super().__init__(message);self.proof=proof or {};self.pending=pending

def vector_dimension(vector):
    if not isinstance(vector,list) or len(vector)!=768 or any(type(v) not in (int,float) or not math.isfinite(v) for v in vector):
        raise GraphGateRefused('actual embedding must contain exactly768 finite values')
    return 768

def _named(rows,name):
    matches=[row for row in rows if row.get('name')==name]
    if len(matches)!=1:raise GraphGateRefused('missing/ambiguous native schema: '+name)
    return matches[0]

def validate_schema(constraints,indexes):
    if type(constraints) is not list or type(indexes) is not list or len(constraints)>128 or len(indexes)>128:
        raise GraphGateRefused('bounded actual schema rows required')
    for name,(label,prop) in CONSTRAINTS.items():
        row=_named(constraints,name)
        if row.get('type') not in ('UNIQUENESS','NODE_PROPERTY_UNIQUENESS') or row.get('entityType')!='NODE' or row.get('labelsOrTypes')!=[label] or row.get('properties')!=[prop]:
            raise GraphGateRefused('native uniqueness definition mismatch: '+name)
    for name,(kind,entity,label,props) in INDEXES.items():
        row=_named(indexes,name)
        if row.get('type')!=kind or row.get('entityType')!=entity or row.get('labelsOrTypes')!=[label] or set(row.get('properties',[]))!=set(props) or len(row.get('properties',[]))!=len(props):
            raise GraphGateRefused('native index definition mismatch: '+name)
        if kind=='VECTOR':
            config=row.get('options',{}).get('indexConfig',{})
            if type(config.get('vector.dimensions')) is not int or config['vector.dimensions']!=768 or (type(config.get('vector.similarity_function')) is not str or config['vector.similarity_function'].casefold()!='cosine'):
                raise GraphGateRefused('native vector dimensions/similarity mismatch: '+name)
        if row.get('state')!='ONLINE':
            raise GraphGateRefused('native index not ONLINE: '+name,pending=row.get('state')=='POPULATING')
    return True

def qualify_graph(driver,queries,schema_sha,embedding,*,deadline,clock=time.monotonic,sleep=time.sleep):
    end=min(deadline,clock()+30)
    proof={'schema_source_sha256':schema_sha,'ddl_count':len(queries),'ddl_consumed':0,'expected_schema_sha256':EXPECTED_SCHEMA_SHA,'expected_ddl_sha256':EXPECTED_DDL_SHA,'expected_constraints':CONSTRAINTS,'expected_indexes':INDEXES,'embedding_dimension':None,'constraints':None,'indexes':None,'capability_queries':[]}
    try:
        proof['embedding_dimension']=vector_dimension(embedding)
        if schema_sha!=EXPECTED_SCHEMA_SHA or len(queries)!=7 or hashlib.sha256(json.dumps(queries,separators=(',',':')).encode()).hexdigest()!=EXPECTED_DDL_SHA:
            raise GraphGateRefused('pinned seven native DDL specs changed')
        from neo4j import Query
        def run(session,text,parameters=None,rows=False):
            remaining=end-clock()
            if remaining<=0:raise GraphGateRefused('graph prerequisite absolute deadline')
            result=session.run(Query(text,timeout=remaining),parameters or {})
            value=result.data() if rows else result.consume()
            if clock()>=end:raise GraphGateRefused('graph prerequisite exceeded absolute deadline')
            return value
        with driver.session() as session:
            for text in queries:
                run(session,text);proof['ddl_consumed']+=1
            while True:
                proof['constraints']=run(session,SHOW_CONSTRAINTS,rows=True)
                proof['indexes']=run(session,SHOW_INDEXES,rows=True)
                try:validate_schema(proof['constraints'],proof['indexes']);break
                except GraphGateRefused as exc:
                    if not exc.pending:raise
                    if clock()>=end:raise GraphGateRefused('native indexes failed to become ONLINE')
                    sleep(min(.05,max(0,end-clock())))
            checks=[("CALL db.index.fulltext.queryNodes('entity_fulltext', 'schema_gate') YIELD node, score RETURN count(*) AS hits",{}),
                    ("CALL db.index.fulltext.queryRelationships('fact_fulltext', 'schema_gate') YIELD relationship, score RETURN count(*) AS hits",{}),
                    ("CALL db.index.vector.queryNodes('entity_embedding', 1, $embedding) YIELD node, score RETURN count(*) AS hits",{'embedding':embedding}),
                    ("CALL db.index.vector.queryRelationships('fact_embedding', 1, $embedding) YIELD relationship, score RETURN count(*) AS hits",{'embedding':embedding})]
            for text,parameters in checks:
                rows=run(session,text,parameters,rows=True)
                if len(rows)!=1 or type(rows[0].get('hits')) is not int or rows[0]['hits']<0:raise GraphGateRefused('native index capability readback malformed')
                proof['capability_queries'].append({'query':text,'rows':rows})
        proof['qualified']=True;return proof
    except Exception as exc:
        proof['qualified']=False;proof['failure_type']=type(exc).__name__;proof['failure']=str(exc)[:1000]
        raise GraphGateRefused('strict native graph prerequisite refused',proof) from exc
