package app.autplay.playback

import kotlinx.coroutines.Job
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test

class ResolutionJobsTest {
    @Test
    fun inlineCompletionCanRemoveJobsDuringGenerationCancellation() {
        val current = Job()
        val next = Job()
        val jobs = linkedMapOf<String, Job>("current" to current, "next" to next)
        jobs.forEach { (entryId, job) ->
            job.invokeOnCompletion {
                if (jobs[entryId] === job) jobs.remove(entryId)
            }
        }

        cancelResolutionJobs(jobs)

        assertTrue(current.isCancelled)
        assertTrue(next.isCancelled)
        assertTrue(jobs.isEmpty())
    }

    @Test
    fun inlineCompletionCannotDiscardOrCancelReplacementRegistration() {
        val previous = Job()
        val replacement = Job()
        val jobs = linkedMapOf<String, Job>("entry" to previous)
        previous.invokeOnCompletion { jobs["entry"] = replacement }

        cancelResolutionJobs(jobs)

        assertTrue(previous.isCancelled)
        assertSame(replacement, jobs["entry"])
        assertTrue(replacement.isActive)
        replacement.cancel()
    }
}
