import { createRoot } from 'react-dom/client';
import App from './App';
import { initStorage } from './lib/storage';
import './styles.css';

const container = document.getElementById('root');
if (!container) throw new Error('Не найден #root');

void initStorage().finally(() => {
  createRoot(container).render(<App />);
});
