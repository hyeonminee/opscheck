"""Answer streaming with gated transports; no paid API or user DB."""
import asyncio
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import httpx

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'eval')]
os.environ.update(LANGSMITH_TRACING='false',LANGCHAIN_TRACING_V2='false')
from agent.openai_llm import OpenAIFastLLM,OpenAIQualityLLM
from agent.execution import GenerationLimitError
from agent.fake_llm import FastLLM,QualityLLM
from agent.deps import SupervisorDeps
from agent.graph import build_graph
from harness import FixedMCP

def event(data):return ('data: '+json.dumps(data,ensure_ascii=False)+'\n\n').encode()

class GatedStream(httpx.AsyncByteStream):
    def __init__(self,ending='stop'):
        self.release=asyncio.Event();self.closed=False;self.ending=ending
    async def __aiter__(self):
        yield event({'choices':[{'delta':{'role':'assistant'},'finish_reason':None}]})
        # UTF-8/SSE may split inside a Korean character or a JSON line.
        data=event({'choices':[{'delta':{'content':'안녕하세요 '},'finish_reason':None}]})
        index=data.index('안'.encode())+1
        yield data[:index];yield data[index:]
        await self.release.wait()
        yield event({'choices':[{'delta':{'content':'확인했습니다.'},'finish_reason':None}]})
        if self.ending=='missing-stop':
            yield b'data: [DONE]\n\n';return
        yield event({'choices':[{'delta':{},'finish_reason':'length' if self.ending=='length' else 'stop'}]})
        yield event({'choices':[],'usage':{'total_tokens':21}})
        if self.ending!='missing-done':yield b'data: [DONE]\n\n'
    async def aclose(self):self.closed=True

class AdapterStreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_chat_and_writer_receive_tokens_before_generation_finishes(self):
        original=httpx.AsyncClient
        for cls in (OpenAIFastLLM,OpenAIQualityLLM):
            with self.subTest(model=cls.__name__):
                stream=GatedStream();captured=[];received=asyncio.Event();pieces=[];clients=[]
                def handle(request):
                    captured.append(json.loads(request.content));return httpx.Response(200,stream=stream)
                def client(**kw):
                    value=original(transport=httpx.MockTransport(handle),**kw);clients.append(value);return value
                def token(piece):pieces.append(piece);received.set()
                model=cls('unit-test',.01);model.api_key='test-placeholder'
                with patch('agent.openai_llm.httpx.AsyncClient',side_effect=client):
                    call=model.chat('인사',[],on_token=token) if cls is OpenAIFastLLM else model.write('진단',[],1,None,on_token=token)
                    task=asyncio.create_task(call)
                    try:
                        await asyncio.wait_for(received.wait(),1)
                        self.assertEqual(pieces,['안녕하세요 ']);self.assertFalse(task.done())
                    finally:stream.release.set()
                    content,usage=await asyncio.wait_for(task,1)
                self.assertEqual(content,''.join(pieces));self.assertEqual(usage['tokens'],21)
                self.assertEqual(captured[0]['stream_options'],{'include_usage':True})
                self.assertTrue(captured[0]['stream']);self.assertTrue(stream.closed);self.assertTrue(clients[0].is_closed)
                if cls is OpenAIQualityLLM:self.assertEqual(captured[0]['response_format'],{'type':'json_object'})

    async def test_incomplete_and_truncated_streams_are_not_success(self):
        original=httpx.AsyncClient
        for ending in ('length','missing-done','missing-stop'):
            with self.subTest(ending=ending):
                stream=GatedStream(ending);stream.release.set()
                model=OpenAIQualityLLM('unit-test',0);model.api_key='test-placeholder'
                with patch('agent.openai_llm.httpx.AsyncClient',side_effect=lambda **kw:original(transport=httpx.MockTransport(lambda request:httpx.Response(200,stream=stream)),**kw)):
                    with self.assertRaises(GenerationLimitError):await model.write('진단',[],1,None,on_token=lambda piece:None)
                self.assertTrue(stream.closed)

    async def test_timeout_and_cancellation_close_stream(self):
        original=httpx.AsyncClient
        for mode in ('timeout','cancel'):
            stream=GatedStream();received=asyncio.Event()
            model=OpenAIFastLLM('unit-test',0);model.api_key='test-placeholder'
            with patch('agent.openai_llm.httpx.AsyncClient',side_effect=lambda **kw:original(transport=httpx.MockTransport(lambda request:httpx.Response(200,stream=stream)),**kw)),patch('agent.openai_llm.MODEL_TIMEOUT',.05 if mode=='timeout' else 2):
                task=asyncio.create_task(model.chat('인사',[],on_token=lambda piece:received.set()))
                await asyncio.wait_for(received.wait(),1)
                if mode=='cancel':task.cancel()
                with self.assertRaises(TimeoutError if mode=='timeout' else asyncio.CancelledError):await task
            self.assertTrue(stream.closed)

    async def test_http_and_stream_errors_do_not_become_completed_answers(self):
        original=httpx.AsyncClient
        for response in (httpx.Response(503,text='unavailable'),httpx.Response(200,content=event({'error':{'message':'private'}}))):
            model=OpenAIFastLLM('unit-test',0);model.api_key='test-placeholder'
            with patch('agent.openai_llm.httpx.AsyncClient',side_effect=lambda **kw:original(transport=httpx.MockTransport(lambda request:response),**kw)):
                with self.assertRaises((httpx.HTTPStatusError,RuntimeError)) as error:await model.chat('인사',[],on_token=lambda piece:None)
                self.assertNotIn('private',str(error.exception))

class DraftPreviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_hypothesis_prefix_arrives_before_writer_release_without_json_or_duplicates(self):
        quality=QualityLLM('fixed',0);original=quality.write
        release=asyncio.Event();received=asyncio.Event();tokens=[]
        hypothesis='관측된 지표와 증상의 관련성을 "추가" 확인해야 합니다.\n후속 확인이 필요합니다. 😀'
        async def write(query,collected,attempt,feedback,on_token=None):
            draft,usage=await original(query,collected,2,feedback)
            data=json.loads(draft);data['analysis'][0]['hypothesis']=hypothesis
            draft=json.dumps(data,ensure_ascii=True)
            boundary=draft.index('hypothesis')+len('hypothesis')+20
            for piece in draft[:boundary]:on_token(piece);await asyncio.sleep(0)
            await release.wait()
            for piece in draft[boundary:]:on_token(piece);await asyncio.sleep(0)
            return draft,usage
        quality.write=write
        deps=SupervisorDeps(FastLLM('fixed',0),quality,FixedMCP(ROOT,{}),None,'v2')
        final={}
        async def consume():
            async for mode,payload in build_graph(deps).astream({'query':'payments-service 진단','history':[],'events':[],'cost_usd':0},stream_mode=['updates','custom']):
                if mode=='custom' and payload.get('type')=='AnswerToken' and payload.get('delta'):
                    self.assertEqual(payload['validation_status'],'pending');tokens.append(payload['delta']);received.set()
                elif mode=='updates':
                    for delta in payload.values():final.update(delta)
        task=asyncio.create_task(consume())
        try:
            await asyncio.wait_for(received.wait(),2)
            self.assertFalse(task.done());self.assertTrue(hypothesis.startswith(''.join(tokens)))
        finally:release.set();await asyncio.wait_for(task,3)
        self.assertEqual(''.join(tokens),hypothesis)
        self.assertEqual(final['validation_status'],'passed')

if __name__=='__main__':unittest.main()
