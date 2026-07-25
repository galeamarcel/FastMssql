use std::future::Future;
use std::time::Duration;
use tokio::time::Instant;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum TimeoutPhase {
    Connect,
    Acquire,
    Operation,
    Transaction,
    Rollback,
}

impl TimeoutPhase {
    pub(crate) const fn as_str(self) -> &'static str {
        match self {
            Self::Connect => "connect",
            Self::Acquire => "acquire",
            Self::Operation => "operation",
            Self::Transaction => "transaction",
            Self::Rollback => "rollback",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum OperationName {
    Connect,
    Ping,
    Query,
    SimpleQuery,
    Execute,
    QueryBatch,
    ExecuteBatch,
    BulkInsert,
    Begin,
    Commit,
    Rollback,
    Close,
    // Reserved for a future public transaction factory that performs I/O.
    // Connection.transaction() is currently synchronous and cannot time out.
    #[allow(dead_code)]
    Transaction,
}

impl OperationName {
    pub(crate) const fn as_str(self) -> &'static str {
        match self {
            Self::Connect => "connect",
            Self::Ping => "ping",
            Self::Query => "query",
            Self::SimpleQuery => "simple_query",
            Self::Execute => "execute",
            Self::QueryBatch => "query_batch",
            Self::ExecuteBatch => "execute_batch",
            Self::BulkInsert => "bulk_insert",
            Self::Begin => "begin",
            Self::Commit => "commit",
            Self::Rollback => "rollback",
            Self::Close => "close",
            Self::Transaction => "transaction",
        }
    }
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct Deadline {
    pub(crate) at: Instant,
    pub(crate) timeout: Duration,
    pub(crate) phase: TimeoutPhase,
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct DeadlineElapsed {
    pub(crate) timeout: Duration,
    pub(crate) phase: TimeoutPhase,
}

pub(crate) fn deadline_from(phase: TimeoutPhase, timeout: Option<Duration>) -> Option<Deadline> {
    timeout.map(|duration| Deadline {
        at: Instant::now() + duration,
        timeout: duration,
        phase,
    })
}

pub(crate) fn earliest_deadline(
    first: Option<Deadline>,
    second: Option<Deadline>,
) -> Option<Deadline> {
    match (first, second) {
        (Some(left), Some(right)) if right.at < left.at => Some(right),
        (Some(left), Some(_)) => Some(left),
        (Some(value), None) | (None, Some(value)) => Some(value),
        (None, None) => None,
    }
}

pub(crate) async fn run_until<F, T>(
    deadline: Option<Deadline>,
    future: F,
) -> Result<T, DeadlineElapsed>
where
    F: Future<Output = T>,
{
    match deadline {
        Some(deadline) => tokio::time::timeout_at(deadline.at, future)
            .await
            .map_err(|_| DeadlineElapsed {
                timeout: deadline.timeout,
                phase: deadline.phase,
            }),
        None => Ok(future.await),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn earliest_absolute_deadline_wins() {
        let now = Instant::now();
        let operation = Deadline {
            at: now + Duration::from_millis(250),
            timeout: Duration::from_millis(250),
            phase: TimeoutPhase::Operation,
        };
        let transaction = Deadline {
            at: now + Duration::from_millis(125),
            timeout: Duration::from_millis(125),
            phase: TimeoutPhase::Transaction,
        };

        let selected = earliest_deadline(Some(operation), Some(transaction))
            .expect("one deadline must be selected");

        assert_eq!(selected.at, transaction.at);
        assert_eq!(selected.timeout, transaction.timeout);
        assert_eq!(selected.phase, TimeoutPhase::Transaction);
    }

    #[test]
    fn phase_and_operation_names_are_stable() {
        let phases = [
            (TimeoutPhase::Connect, "connect"),
            (TimeoutPhase::Acquire, "acquire"),
            (TimeoutPhase::Operation, "operation"),
            (TimeoutPhase::Transaction, "transaction"),
            (TimeoutPhase::Rollback, "rollback"),
        ];
        for (phase, expected) in phases {
            assert_eq!(phase.as_str(), expected);
        }

        let operations = [
            (OperationName::Connect, "connect"),
            (OperationName::Ping, "ping"),
            (OperationName::Query, "query"),
            (OperationName::SimpleQuery, "simple_query"),
            (OperationName::Execute, "execute"),
            (OperationName::QueryBatch, "query_batch"),
            (OperationName::ExecuteBatch, "execute_batch"),
            (OperationName::BulkInsert, "bulk_insert"),
            (OperationName::Begin, "begin"),
            (OperationName::Commit, "commit"),
            (OperationName::Rollback, "rollback"),
            (OperationName::Close, "close"),
            (OperationName::Transaction, "transaction"),
        ];
        for (operation, expected) in operations {
            assert_eq!(operation.as_str(), expected);
        }
    }

    #[test]
    fn run_until_reports_the_selected_absolute_deadline() {
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_time()
            .build()
            .expect("the test runtime must build");

        runtime.block_on(async {
            let deadline = Deadline {
                at: Instant::now() + Duration::from_millis(5),
                timeout: Duration::from_millis(5),
                phase: TimeoutPhase::Operation,
            };

            let elapsed = run_until(Some(deadline), async {
                tokio::time::sleep(Duration::from_secs(1)).await;
            })
            .await
            .expect_err("the operation must exceed the deadline");

            assert_eq!(elapsed.timeout, Duration::from_millis(5));
            assert_eq!(elapsed.phase, TimeoutPhase::Operation);
        });
    }
}
