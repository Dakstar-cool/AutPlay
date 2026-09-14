package app.autplay.playback

import kotlinx.coroutines.Job

/** Cancels a generation of resolution jobs on their owning player looper. */
internal fun cancelResolutionJobs(jobs: MutableMap<String, Job>) {
    // Completion callbacks may run inline and mutate the registry, including registering new jobs.
    val previousJobs = jobs.values.toList()
    jobs.clear()
    previousJobs.forEach(Job::cancel)
}
