"""SubAgent: a scoped worker that communicates via inbox, not streaming.

SubAgent shares the parent's model and skill registry but has its own
inbox, message stores, and tool set. It runs an autonomous loop as an
asyncio.Task, posting results to the parent's inbox thread.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, TYPE_CHECKING

from .inbox import Inbox, ThreadStatus
from .messages import Message, MessageType, SubAgentContext

if TYPE_CHECKING:
    from .agent import Agent

logger = logging.getLogger(__name__)


class SubAgent:
    """A scoped worker agent that communicates via inbox.

    Shares the parent's model and skill registry. Does not stream events
    to consumers — communicates exclusively through inbox threads.
    """

    def __init__(
        self,
        *,
        parent: "Agent",
        instructions: str,
        system_prompt: str,
        tools: list[str],
        skills: list[str],
        thread_id: str,
    ) -> None:
        self.parent = parent
        self.instructions = instructions
        self.system_prompt = system_prompt
        self.requested_tools = tools
        self.requested_skills = skills
        self.thread_id = thread_id

        # Own stores
        self.inbox = Inbox()
        self.message_log: list[Message] = []
        self.context_window: list[Message] = []

        # Resolved from parent's registry
        self._skills = {
            name: parent._skills[name]
            for name in skills
            if name in parent._skills
        }

        self._source_context = SubAgentContext(
            subagent_id=thread_id,
            parent_interaction_id=thread_id,
            sender=f"subagent:{thread_id[:8]}",
        )

        self._done = False

    @property
    def is_done(self) -> bool:
        return self._done

    def _check_thread_alive(self) -> bool:
        """Check if our thread still exists in the parent's inbox."""
        thread = self.parent.inbox.get_thread(self.thread_id)
        if thread.status == ThreadStatus.done and not thread.items:
            return False
        return True

    def _post_to_parent(
        self,
        content: str,
        subject: str | None = None,
        notify: bool = False,
        status: ThreadStatus | None = None,
    ) -> None:
        """Write a message to the parent's inbox thread."""
        self.parent.inbox.write_to_thread(
            thread_id=self.thread_id,
            content=content,
            source_context=self._source_context,
            notify=notify,
            subject=subject,
            status=status,
        )

    async def run_loop(self) -> None:
        """The autonomous subagent loop.

        1. Run with instructions as initial prompt
        2. Check inbox between steps
        3. Wind down when thread is deleted
        """
        try:
            self._post_to_parent(
                content="Subagent started.",
                subject=self.instructions[:80],
                notify=False,
                status=ThreadStatus.in_progress,
            )

            # Initial run with instructions
            await self._execute_step(self.instructions)

            # Autonomous loop: check inbox, process, repeat
            while not self._done:
                if not self._check_thread_alive():
                    logger.info("SubAgent %s: thread deleted, winding down.", self.thread_id[:8])
                    self._done = True
                    break

                # Check own inbox for new messages
                unread = self.inbox.read_inbox()
                if unread:
                    for item in unread:
                        await self._execute_step(item.content)
                else:
                    await asyncio.sleep(0.5)

                if not self._check_thread_alive():
                    self._done = True
                    break

        except Exception as e:
            logger.error("SubAgent %s error: %s", self.thread_id[:8], e)
            self._post_to_parent(
                content=f"Subagent error: {e}",
                notify=True,
                status=ThreadStatus.done,
            )
        finally:
            self._done = True

    async def _execute_step(self, prompt: str) -> None:
        """Execute one step using the parent's model.

        Posts results to the parent thread. A full implementation would
        create its own pydantic-ai runner with filtered tools.
        """
        self.message_log.append(Message(type=MessageType.user, content=prompt))
        self.context_window.append(Message(type=MessageType.user, content=prompt))

        self._post_to_parent(
            content=f"Processing: {prompt[:200]}",
            notify=False,
            status=ThreadStatus.in_progress,
        )

    async def finish(self, result: str) -> None:
        """Mark the subagent as done and post the final result."""
        self._done = True
        self._post_to_parent(
            content=result,
            notify=True,
            status=ThreadStatus.done,
        )
