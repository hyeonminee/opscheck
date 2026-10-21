"""Capture real-adapter HTTP payloads without any live API calls."""
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import httpx

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ.update(LANGCHAIN_TRACING_V2='false',LANGSMITH_TRACING='false')
from agent.ollama_llm import OllamaFastLLM, OllamaQualityLLM
from agent.openai_llm import OpenAIFastLLM, OpenAIQualityLLM
from agent.fake_llm import FastLLM


class ChatMessageTests(unittest.IsolatedAsyncioTestCase):
    async def capture(self, backend, query, history, quality=False):
        payloads=[]
        def handle(request):
            payloads.append(json.loads(request.content))
            if backend=='ollama':
                lines=[{'message':{'content':'현민님이라고 '},'done':False},
                       {'message':{'content':'말씀하셨어요.'},'done':True,'prompt_eval_count':10,'eval_count':5}]
                return httpx.Response(200,text='\n'.join(json.dumps(l,ensure_ascii=False) for l in lines))
            return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':'현민님이라고 말씀하셨어요.'}}], 'usage':{'total_tokens':15}})
        transport=httpx.MockTransport(handle)
        pieces=[]
        if backend=='ollama':
            cls=OllamaQualityLLM if quality else OllamaFastLLM
            model=cls('unit-test',0)
            original=httpx.AsyncClient
            with patch('agent.ollama_llm.httpx.AsyncClient',side_effect=lambda **kw: original(transport=transport,**kw)):
                result=await model.write(query,[],1,None,on_token=pieces.append) if quality else await model.chat(query,history,on_token=pieces.append)
        else:
            cls=OpenAIQualityLLM if quality else OpenAIFastLLM
            model=cls('unit-test',0)
            model.api_key='unit-test-placeholder'
            original=httpx.AsyncClient
            with patch('agent.openai_llm.httpx.AsyncClient',side_effect=lambda **kw: original(transport=transport,**kw)):
                result=await model.write(query,[],1,None,on_token=pieces.append) if quality else await model.chat(query,history,on_token=pieces.append)
        self.assertEqual(''.join(pieces),result[0])
        self.assertEqual(result[1]['tokens'],15)
        return payloads[0],result[1]

    async def test_both_adapters_preserve_roles_and_latest_query(self):
        history=[{'role':'user','content':'내 이름은 현민입니다'},
                 {'role':'assistant','content':'현민님, 반갑습니다.'}]
        for backend in ['ollama','openai']:
            with self.subTest(backend=backend):
                payload,usage=await self.capture(backend,'제 이름이 뭐였죠?',history)
                self.assertEqual([m['role'] for m in payload['messages']],['system','user','assistant','user'])
                self.assertEqual(payload['messages'][1:],history+[{'role':'user','content':'제 이름이 뭐였죠?'}])
                self.assertIn('[ASSISTANT]\n현민님, 반갑습니다.',usage['prompt'])
                self.assertNotIn('## 지금까지의 대화',payload['messages'][-1]['content'])

    async def test_empty_history_is_not_invented(self):
        for backend in ['ollama','openai']:
            payload,_=await self.capture(backend,'제 이름이 뭐였죠?',[])
            self.assertEqual([m['role'] for m in payload['messages']],['system','user'])
            self.assertEqual(payload['messages'][-1]['content'],'제 이름이 뭐였죠?')

    async def test_recent_history_limit_and_role_filter(self):
        history=[{'role':'user' if i%2==0 else 'assistant','content':str(i)} for i in range(12)]
        for backend in ['ollama','openai']:
            payload,_=await self.capture(backend,'새 질문',history)
            self.assertEqual(payload['messages'][1:-1],history[-10:])
            payload,_=await self.capture(backend,'새 질문',[{'role':'system','content':'stored instruction'},history[-1]])
            self.assertEqual(payload['messages'][1:-1],[history[-1]])

    async def test_report_writer_keeps_its_existing_two_message_contract(self):
        for backend in ['ollama','openai']:
            payload,_=await self.capture(backend,'payments-service 진단',[],quality=True)
            self.assertEqual([m['role'] for m in payload['messages']],['system','user'])
            self.assertIn('payments-service 진단',payload['messages'][-1]['content'])

    async def test_fake_understands_both_name_introductions_and_user_only_history(self):
        model=FastLLM('fixed',0)
        for prefix in ['제','내']:
            intro=f'{prefix} 이름은 현민입니다'
            reply,_=await model.chat(intro,[])
            self.assertIn('현민님',reply)
            reply,_=await model.chat('제 이름이 뭐였죠?',[{'role':'user','content':intro},
                {'role':'assistant','content':'제 이름은 민수입니다'}])
            self.assertIn('현민님',reply)
            self.assertNotIn('민수',reply)
        reply,_=await model.chat('제 이름이 뭐였죠?',[])
        self.assertIn('확인하지 못',reply)
        self.assertNotIn('저의 이름',reply)


class NameMemoryChecks(unittest.TestCase):
    def test_latest_user_name_wins_over_assistant_claim(self):
        from agent.chat_memory import name_reply
        history=[{'role':'user','content':'내 이름은 현민입니다'},
                 {'role':'assistant','content':'제 이름은 민수입니다'}]
        self.assertEqual(name_reply('제 이름이 뭐였죠?',history),'현민님이라고 말씀하셨어요.')
        history.append({'role':'user','content':'제 이름은 지원입니다'})
        self.assertEqual(name_reply('제 이름이 뭐였죠?',history),'지원님이라고 말씀하셨어요.')

    def test_no_cross_session_or_old_window_guess(self):
        from agent.chat_memory import name_reply
        self.assertIn('확인하지 못',name_reply('제 이름이 뭐였죠?',[]))
        history=[{'role':'user','content':'내 이름은 현민입니다'}]+[{'role':'user','content':'안녕'}]*10
        self.assertNotIn('현민',name_reply('제 이름이 뭐였죠?',history))

    def test_unrelated_or_mixed_request_stays_with_normal_routing(self):
        from agent.chat_memory import name_reply
        for query in ['로그인이 느려요','내 이름은 현민입니다. payments-service 확인해줘','오늘 무엇을 공부할까?']:
            self.assertIsNone(name_reply(query,[]))


if __name__=='__main__':
    unittest.main()
