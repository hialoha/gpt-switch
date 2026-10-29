import unittest
from unittest.mock import patch, MagicMock
import discovery


class DiscoveryTests(unittest.TestCase):
    def test_api_default_not_first_item(self):
        self.assertEqual(discovery.resolve({},[{'model':'old'},{'model':'recommended','isDefault':True}])[:2],('recommended','openai'))

    def test_effective_config_overrides_recommendation(self):
        self.assertEqual(discovery.resolve({'model':'chosen','model_provider':'custom'},[{'model':'recommended','isDefault':True}])[:2],('chosen','custom'))

    def test_ambiguous_default_requires_selection(self):
        self.assertEqual(discovery.resolve({},[{'model':'first'}])[0],'')
        self.assertEqual(discovery.resolve({},[{'model':'one','isDefault':True},{'model':'two','isDefault':True}])[0],'')

    def test_handshake_pagination_cleanup(self):
        client=MagicMock()
        client.call.side_effect=[{}, {'config':{'model_provider':'custom'}},
            {'data':[{'model':'first'}],'nextCursor':'page2'},
            {'data':[{'model':'default','isDefault':True}], 'nextCursor':None}]
        with patch('discovery.Client',return_value=client):
            result=discovery.discover('unused')
        self.assertEqual(result['model'],'default')
        self.assertEqual(result['provider'],'custom')
        self.assertEqual(result['values'],['first','default'])
        client.send.assert_called_once_with({'method':'initialized','params':{}})
        self.assertEqual(client.call.call_args_list[-1].args[1]['cursor'],'page2')
        client.close.assert_called_once()

    def test_rpc_error_always_closes_child(self):
        client=MagicMock(); client.call.side_effect=RuntimeError('unavailable')
        with patch('discovery.Client',return_value=client):
            with self.assertRaises(RuntimeError): discovery.discover('unused')
        client.close.assert_called_once()


if __name__=='__main__': unittest.main()
