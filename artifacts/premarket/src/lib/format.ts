import { format } from "date-fns";

export function formatDate(dateString: string) {
  try {
    return format(new Date(dateString), "MMM dd, yyyy");
  } catch (e) {
    return dateString;
  }
}

export function formatDateTime(dateString: string) {
  try {
    return format(new Date(dateString), "MMM dd, yyyy HH:mm");
  } catch (e) {
    return dateString;
  }
}
