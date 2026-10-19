from ch3.vlm.client import DashScopeVLMClient


def test_thinking_control_is_explicit_and_default_payload_unchanged():
    class Transport:
        def chat(self, payload):
            self.payload = payload
            return {'choices': [{'message': {'content': '{"actions":[]}'},
                                  'finish_reason': 'stop'}]}
    transport = Transport()
    client = DashScopeVLMClient(transport=transport)
    client.complete(system_prompt='schema', user_prompt='task')
    assert 'enable_thinking' not in transport.payload
    client.complete(system_prompt='schema', user_prompt='task', enable_thinking=False)
    assert transport.payload['enable_thinking'] is False
