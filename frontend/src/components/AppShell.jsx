import { useWorkspace } from "../context/WorkspaceContext";
import AssignmentBar from "./AssignmentBar";
import Navbar from "./Navbar";
import { ErrorState, LoadingState } from "./States";

/** Frame of every signed-in page: navigation, assignment context, content. */
export default function AppShell({ children }) {
  const { loading, error, reload, surveyor } = useWorkspace();

  return (
    <div className="app-shell">
      <Navbar />
      {surveyor?.is_dev_session ? (
        <div className="dev-banner" role="status">
          Development session: authentication is switched off on this server. Do not use this mode
          with real survey data.
        </div>
      ) : null}
      {loading && !surveyor ? (
        <main className="page">
          <LoadingState label="Loading your assignment" />
        </main>
      ) : error && !surveyor ? (
        <main className="page">
          <div className="panel">
            <ErrorState title="Your profile could not be loaded" detail={error} onRetry={reload} />
          </div>
        </main>
      ) : (
        <>
          <AssignmentBar />
          {children}
        </>
      )}
    </div>
  );
}
