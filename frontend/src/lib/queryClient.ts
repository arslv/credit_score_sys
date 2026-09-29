import { QueryClient } from "@tanstack/react-query";

// Только память — никакого persister-плагина (localforage/localStorage) не
// подключаем нигде в проекте. Персданные заявок не должны пережить закрытие
// вкладки (ваше уточнение). При логауте и при 401 вызывающий код обязан
// вызвать queryClient.clear() — см. src/auth/AuthProvider.tsx.
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: false,
      refetchOnWindowFocus: false,
    },
  },
});
