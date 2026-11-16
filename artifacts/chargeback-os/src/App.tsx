import { type ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ErrorBoundary } from '@/components/error-boundary';
import { Toaster } from '@/components/ui/toaster';
import { TooltipProvider } from '@/components/ui/tooltip';
import NotFound from '@/pages/not-found';
import OfficePage from '@/pages/office';
import DisputesPage from '@/pages/disputes';
import SettingsPage from '@/pages/settings';
import SandboxPage from '@/pages/sandbox';
import SandboxRunPage from '@/pages/sandbox-run';
import SandboxExamplePage from '@/pages/sandbox-example';
import SandboxGuidePage from '@/pages/sandbox-guide';
import { SandboxCredentials } from '@/hooks/use-sandbox';
import '@/sandbox.css';
import { Route, Switch, useLocation, Router as WouterRouter } from 'wouter';

const queryClient = new QueryClient();

function Router() {
  return (
    <RoutedErrorBoundary>
      <Switch>
        <Route path="/" component={SandboxPage} />
        <Route path="/runs/:id" component={SandboxRunPage} />
        <Route path="/guide" component={SandboxGuidePage} />
        <Route path="/example" component={SandboxExamplePage} />
        <Route path="/office" component={OfficePage} />
        <Route path="/disputes" component={DisputesPage} />
        <Route path="/disputes/:id" component={DisputesPage} />
        <Route path="/settings" component={SettingsPage} />
        <Route component={NotFound} />
      </Switch>
    </RoutedErrorBoundary>
  );
}

function RoutedErrorBoundary({ children }: { children: ReactNode }) {
  const [location] = useLocation();
  return <ErrorBoundary resetKey={location}>{children}</ErrorBoundary>;
}

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <WouterRouter base={import.meta.env.BASE_URL.replace(/\/$/, '')}>
          <SandboxCredentials><Router /></SandboxCredentials>
        </WouterRouter>
        <Toaster />
      </TooltipProvider>
    </QueryClientProvider>
  );
}

export default App;
