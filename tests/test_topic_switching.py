"""Topic changes must route as commands and invalidate prior authoring context."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from app.services.topic_workflow import handle_topic_message
from app.services.draft_workflow import handle_draft_message


class Context:
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        return False
    async def start_transaction(self):
        return Context()


def database(workflow):
    collection = lambda: SimpleNamespace(find_one=AsyncMock(), update_one=AsyncMock(return_value=SimpleNamespace(modified_count=1)), update_many=AsyncMock())
    db = SimpleNamespace(workflows=collection(), messages=collection(), jobs=collection(), approval_receipts=collection(),
                         client=SimpleNamespace(start_session=lambda: Context()))
    db.workflows.find_one.return_value = workflow
    db.messages.find_one.return_value = {'_id':'message'}
    return db


def workflow(state):
    return {'_id':'workflow', 'state':state, 'revision':8, 'shortlist_revision':1,
            'shortlist':[{'title':'First'}, {'title':'Second'}], 'selected_topic':{'title':'First'},
            'draft_version':2, 'draft_id':'draft-old', 'approval_id':'approval-old', 'preview_id':'preview-old'}


class TopicSwitchTests(unittest.IsolatedAsyncioTestCase):
    async def test_choose_routes_out_of_draft_feedback_at_each_safe_stage(self):
        for state in ('READY_FOR_DRAFT','DRAFT_GENERATING','DRAFT_REVIEW','AWAITING_REVIEW',
                      'AWAITING_APPROVAL','PREVIEW_SENDING','PUBLISH_PENDING'):
            db = database(workflow(state))
            self.assertIsNone(await handle_draft_message(db, {'_id':'message','text':'choose 2'}, 'installation'))
            db.workflows.update_one.assert_not_awaited()

    async def test_switch_resets_context_cancels_pending_work_and_keeps_history(self):
        for state in ('READY_FOR_DRAFT','DRAFT_GENERATING','DRAFT_REVIEW','AWAITING_APPROVAL','PUBLISH_PENDING'):
            db = database(workflow(state))
            with patch('app.services.topic_workflow.queue_message', AsyncMock()) as reply:
                self.assertEqual(await handle_topic_message(db, {'_id':'message','text':'choose 2'}, 'installation'), 'topic_processed')
            change = db.workflows.update_one.call_args.args[1]['$set']
            self.assertEqual(change['selected_topic']['title'], 'Second')
            self.assertEqual(change['state'], 'AWAITING_INPUT')
            self.assertIsNone(change['perspective'])
            self.assertIsNone(change['draft_id'])
            self.assertIsNone(change['generation_id'])
            self.assertIsNone(change['pending_preview'])
            self.assertNotIn('draft_version', change)
            self.assertEqual(change['topic_selection_revision'], 2)
            self.assertEqual(db.jobs.update_many.await_count, 2)
            db.approval_receipts.update_one.assert_awaited_once()
            self.assertIn('Selected: Second', reply.call_args.args[3])

    async def test_stale_or_invalid_choice_preserves_current_topic(self):
        for text in ('CHOOSE 0:2', 'CHOOSE 9', 'CHOOSE banana'):
            db = database(workflow('DRAFT_REVIEW'))
            with patch('app.services.topic_workflow.queue_message', AsyncMock()):
                await handle_topic_message(db, {'_id':'message','text':text}, 'installation')
            change = db.workflows.update_one.call_args.args[1]['$set']
            self.assertNotIn('selected_topic', change)
            db.jobs.update_many.assert_not_awaited()

    async def test_inflight_publication_remains_blocked(self):
        for state in ('PUBLISHING', 'PUBLISH_UNKNOWN'):
            db = database(workflow(state))
            with patch('app.services.draft_workflow._consume_with_reply', AsyncMock()):
                result = await handle_draft_message(db, {'_id':'message','text':'CHOOSE 2'}, 'installation')
            self.assertIn(result, ('publication_in_progress','publication_unknown'))
            db.workflows.update_one.assert_not_awaited()

    async def test_restore_from_previous_topic_is_blocked(self):
        db = database({**workflow('DRAFT_REVIEW'), 'topic_selection_revision':2})
        db.draft_versions = SimpleNamespace(find_one=AsyncMock(return_value={'version':1, 'body':'Old topic'}))
        with patch('app.services.draft_workflow._consume_with_reply', AsyncMock()) as reply:
            await handle_draft_message(db, {'_id':'message','text':'RESTORE 1'}, 'installation')
        self.assertIn('earlier topic', reply.call_args.args[3])
        db.workflows.update_one.assert_not_awaited()
