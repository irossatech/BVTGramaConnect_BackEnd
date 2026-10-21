"""Signals other apps listen to (notifications sends pushes and inbox items).

posts.services sends them with transaction.on_commit, so receivers only ever
see changes that were saved. Receivers must not raise.

- post_published(sender, post)                 post became PUBLISHED
- post_submitted(sender, post)                 post entered PENDING
- post_decided(sender, post, action, actor)    a reviewer acted: publish, recheck,
                                               discard, unpublish or reverse
- post_escalated(sender, post, level)          overdue PENDING post; `level` is the
                                               AreaLevel of the admins to alert
"""

from django.dispatch import Signal

post_published = Signal()
post_submitted = Signal()
post_decided = Signal()
post_escalated = Signal()
