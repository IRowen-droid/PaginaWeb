
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from datetime import datetime
import mysql.connector
from decimal import Decimal, InvalidOperation

# ---------------------------
# Database configuration
# ---------------------------
class DBConfig:
    HOST = "localhost"
    USER = "root"
    PASSWORD = "admin123"   # change if you have a password
    DATABASE = "miscelanea_don_papu"

# ---------------------------
# Database handler
# ---------------------------
class DBHandler:
    def __init__(self, cfg: DBConfig):
        self.cfg = cfg

    def connect(self):
        try:
            return mysql.connector.connect(
                host=self.cfg.HOST,
                user=self.cfg.USER,
                password=self.cfg.PASSWORD,
                database=self.cfg.DATABASE,
                autocommit=False
            )
        except mysql.connector.Error as e:
            messagebox.showerror("DB Error", f"Cannot connect to database: {e}")
            return None

    # Inventory queries
    def get_all_products(self):
        con = self.connect()
        if not con:
            return []
        cur = con.cursor()
        cur.execute("SELECT id_producto, nombre, precio_compra, precio_venta, cantidad, sku FROM producto ORDER BY id_producto")
        rows = cur.fetchall()
        con.close()
        return rows

    def add_product(self, nombre, categoria, precio_compra, precio_venta, cantidad, sku):
        con = self.connect()
        if not con:
            return False
        try:
            cur = con.cursor()
            cur.execute("""
                INSERT INTO producto (nombre, categoria, precio_compra, precio_venta, cantidad, sku)
                VALUES (%s,%s,%s,%s,%s,%s)
            """, (nombre, categoria, precio_compra, precio_venta, cantidad, sku))
            con.commit()
            return True
        except mysql.connector.Error as e:
            con.rollback()
            messagebox.showerror("DB Error", f"Error adding product: {e}")
            return False
        finally:
            con.close()

    def update_product_quantity(self, id_producto, new_quantity):
        con = self.connect()
        if not con:
            return False
        try:
            cur = con.cursor()
            cur.execute("UPDATE producto SET cantidad=%s WHERE id_producto=%s", (new_quantity, id_producto))
            con.commit()
            return True
        except mysql.connector.Error as e:
            con.rollback()
            messagebox.showerror("DB Error", f"Error updating quantity: {e}")
            return False
        finally:
            con.close()

    def get_product_by_id(self, id_producto):
        con = self.connect()
        if not con:
            return None
        cur = con.cursor()
        cur.execute("SELECT id_producto, nombre, precio_compra, precio_venta, cantidad, sku FROM producto WHERE id_producto=%s", (id_producto,))
        row = cur.fetchone()
        con.close()
        return row

    # Sales transaction: create venta and venta_detalle, deduct inventory
    def create_sale(self, items):
        """
        items: list of dicts: {id_producto, cantidad, precio_unitario}
        Returns sale_id on success, None on failure.
        """
        con = self.connect()
        if not con:
            return None
        try:
            cur = con.cursor()
            # Check stock for all items first
            for it in items:
                cur.execute("SELECT cantidad FROM producto WHERE id_producto=%s FOR UPDATE", (it['id_producto'],))
                row = cur.fetchone()
                if not row:
                    raise ValueError(f"Producto ID {it['id_producto']} no existe")
                stock = row[0]
                if it['cantidad'] > stock:
                    raise ValueError(f"Stock insuficiente para producto ID {it['id_producto']} (disponible {stock})")

            # Calculate total
            total = Decimal('0.00')
            for it in items:
                subtotal = Decimal(str(it['precio_unitario'])) * Decimal(it['cantidad'])
                it['subtotal'] = subtotal.quantize(Decimal('0.01'))
                total += it['subtotal']

            # Insert venta
            cur.execute("INSERT INTO venta (fecha, total) VALUES (NOW(), %s)", (str(total),))
            sale_id = cur.lastrowid

            # Insert detalles and update stock
            for it in items:
                cur.execute("""
                    INSERT INTO venta_detalle (id_venta, id_producto, cantidad, precio_unitario, subtotal)
                    VALUES (%s,%s,%s,%s,%s)
                """, (sale_id, it['id_producto'], it['cantidad'], str(it['precio_unitario']), str(it['subtotal'])))
                # Deduct stock
                cur.execute("UPDATE producto SET cantidad = cantidad - %s WHERE id_producto=%s", (it['cantidad'], it['id_producto']))

            con.commit()
            return sale_id
        except Exception as e:
            con.rollback()
            messagebox.showerror("Venta Error", f"No se pudo completar la venta: {e}")
            return None
        finally:
            con.close()

    # Reports
    def get_sales(self, start_date=None, end_date=None):
        con = self.connect()
        if not con:
            return []
        cur = con.cursor()
        if start_date and end_date:
            cur.execute("""
                SELECT v.id_venta, v.fecha, v.total, d.id_detalle, d.id_producto, p.nombre, d.cantidad, d.precio_unitario, d.subtotal
                FROM venta v
                JOIN venta_detalle d ON v.id_venta = d.id_venta
                JOIN producto p ON d.id_producto = p.id_producto
                WHERE v.fecha BETWEEN %s AND %s
                ORDER BY v.fecha DESC, v.id_venta DESC
            """, (start_date, end_date))
        else:
            cur.execute("""
                SELECT v.id_venta, v.fecha, v.total, d.id_detalle, d.id_producto, p.nombre, d.cantidad, d.precio_unitario, d.subtotal
                FROM venta v
                JOIN venta_detalle d ON v.id_venta = d.id_venta
                JOIN producto p ON d.id_producto = p.id_producto
                ORDER BY v.fecha DESC, v.id_venta DESC
            """)
        rows = cur.fetchall()
        con.close()
        return rows

# ---------------------------
# GUI Application
# ---------------------------
class POSApp:
    def __init__(self, root, db: DBHandler):
        self.root = root
        self.db = db
        self.root.title("POS - Miscelanea Don Papu")
        self.root.geometry("1100x650")
        self.cart = []  # list of dicts {id_producto, nombre, cantidad, precio_unitario, subtotal}

        self.create_widgets()
        self.load_products()

    def create_widgets(self):
        tab_control = ttk.Notebook(self.root)
        self.tab_inventory = ttk.Frame(tab_control)
        self.tab_sales = ttk.Frame(tab_control)
        self.tab_reports = ttk.Frame(tab_control)

        tab_control.add(self.tab_inventory, text="Inventory")
        tab_control.add(self.tab_sales, text="Sales")
        tab_control.add(self.tab_reports, text="Reports")
        tab_control.pack(expand=1, fill="both")

        self.build_inventory_tab()
        self.build_sales_tab()
        self.build_reports_tab()

    # ---------------------------
    # Inventory Tab
    # ---------------------------
    def build_inventory_tab(self):
        frame = self.tab_inventory

        # Treeview
        cols = ("ID", "Nombre", "Compra", "Venta", "Cantidad", "SKU")
        self.inv_tree = ttk.Treeview(frame, columns=cols, show="headings")
        for c in cols:
            self.inv_tree.heading(c, text=c)
            self.inv_tree.column(c, width=120)
        self.inv_tree.place(x=10, y=10, width=760, height=520)

        # Controls
        control_frame = ttk.LabelFrame(frame, text="Product")
        control_frame.place(x=780, y=10, width=300, height=300)

        ttk.Label(control_frame, text="Nombre").grid(row=0, column=0, sticky="e", padx=5, pady=5)
        ttk.Label(control_frame, text="Categoria").grid(row=1, column=0, sticky="e", padx=5, pady=5)
        ttk.Label(control_frame, text="Precio Compra").grid(row=2, column=0, sticky="e", padx=5, pady=5)
        ttk.Label(control_frame, text="Precio Venta").grid(row=3, column=0, sticky="e", padx=5, pady=5)
        ttk.Label(control_frame, text="Cantidad").grid(row=4, column=0, sticky="e", padx=5, pady=5)
        ttk.Label(control_frame, text="SKU").grid(row=5, column=0, sticky="e", padx=5, pady=5)

        self.i_nombre = ttk.Entry(control_frame)
        self.i_categoria = ttk.Entry(control_frame)
        self.i_precio_compra = ttk.Entry(control_frame)
        self.i_precio_venta = ttk.Entry(control_frame)
        self.i_cantidad = ttk.Entry(control_frame)
        self.i_sku = ttk.Entry(control_frame)

        self.i_nombre.grid(row=0, column=1, padx=5, pady=3)
        self.i_categoria.grid(row=1, column=1, padx=5, pady=3)
        self.i_precio_compra.grid(row=2, column=1, padx=5, pady=3)
        self.i_precio_venta.grid(row=3, column=1, padx=5, pady=3)
        self.i_cantidad.grid(row=4, column=1, padx=5, pady=3)
        self.i_sku.grid(row=5, column=1, padx=5, pady=3)

        ttk.Button(control_frame, text="Add Product", command=self.add_product).grid(row=6, column=1, pady=8)
        ttk.Button(control_frame, text="Refresh", command=self.load_products).grid(row=7, column=1, pady=4)
        ttk.Button(control_frame, text="Edit Selected", command=self.edit_selected_product).grid(row=8, column=1, pady=4)
        ttk.Button(control_frame, text="Delete Selected", command=self.delete_selected_product).grid(row=9, column=1, pady=4)

    def load_products(self):
        for i in self.inv_tree.get_children():
            self.inv_tree.delete(i)
        rows = self.db.get_all_products()
        for r in rows:
            self.inv_tree.insert("", tk.END, values=r)

    def add_product(self):
        try:
            nombre = self.i_nombre.get().strip()
            categoria = self.i_categoria.get().strip()
            precio_compra = Decimal(self.i_precio_compra.get())
            precio_venta = Decimal(self.i_precio_venta.get())
            cantidad = int(self.i_cantidad.get())
            sku = self.i_sku.get().strip() or None

            if not nombre:
                raise ValueError("Nombre requerido")

            ok = self.db.add_product(nombre, categoria, precio_compra, precio_venta, cantidad, sku)
            if ok:
                messagebox.showinfo("OK", "Producto agregado")
                self.clear_product_form()
                self.load_products()
        except (InvalidOperation, ValueError) as e:
            messagebox.showerror("Input Error", f"Datos inválidos: {e}")

    def clear_product_form(self):
        self.i_nombre.delete(0, tk.END)
        self.i_categoria.delete(0, tk.END)
        self.i_precio_compra.delete(0, tk.END)
        self.i_precio_venta.delete(0, tk.END)
        self.i_cantidad.delete(0, tk.END)
        self.i_sku.delete(0, tk.END)

    def edit_selected_product(self):
        sel = self.inv_tree.selection()
        if not sel:
            messagebox.showwarning("Select", "Seleccione un producto")
            return
        item = self.inv_tree.item(sel[0])['values']
        idp = item[0]
        prod = self.db.get_product_by_id(idp)
        if not prod:
            messagebox.showerror("Error", "Producto no encontrado")
            return

        # Simple edit dialog for quantity and prices
        new_nombre = simpledialog.askstring("Edit Nombre", "Nombre:", initialvalue=prod[1])
        if new_nombre is None:
            return
        try:
            new_precio_compra = Decimal(simpledialog.askstring("Edit Compra", "Precio compra:", initialvalue=str(prod[2])))
            new_precio_venta = Decimal(simpledialog.askstring("Edit Venta", "Precio venta:", initialvalue=str(prod[3])))
            new_cantidad = int(simpledialog.askstring("Edit Cantidad", "Cantidad:", initialvalue=str(prod[4])))
        except Exception as e:
            messagebox.showerror("Input Error", f"Datos inválidos: {e}")
            return

        con = self.db.connect()
        if not con:
            return
        try:
            cur = con.cursor()
            cur.execute("""
                UPDATE producto SET nombre=%s, precio_compra=%s, precio_venta=%s, cantidad=%s WHERE id_producto=%s
            """, (new_nombre, str(new_precio_compra), str(new_precio_venta), new_cantidad, idp))
            con.commit()
            messagebox.showinfo("OK", "Producto actualizado")
            self.load_products()
        except mysql.connector.Error as e:
            con.rollback()
            messagebox.showerror("DB Error", f"Error actualizando producto: {e}")
        finally:
            con.close()

    def delete_selected_product(self):
        sel = self.inv_tree.selection()
        if not sel:
            messagebox.showwarning("Select", "Seleccione un producto")
            return
        item = self.inv_tree.item(sel[0])['values']
        idp = item[0]
        if not messagebox.askyesno("Confirm", f"Eliminar producto ID {idp}?"):
            return
        con = self.db.connect()
        if not con:
            return
        try:
            cur = con.cursor()
            cur.execute("DELETE FROM producto WHERE id_producto=%s", (idp,))
            con.commit()
            messagebox.showinfo("OK", "Producto eliminado")
            self.load_products()
        except mysql.connector.Error as e:
            con.rollback()
            messagebox.showerror("DB Error", f"Error eliminando producto: {e}")
        finally:
            con.close()

    # ---------------------------
    # Sales Tab
    # ---------------------------
    def build_sales_tab(self):
        frame = self.tab_sales

        # Left: product list
        cols = ("ID", "Nombre", "Venta", "Cantidad")
        self.sales_tree = ttk.Treeview(frame, columns=cols, show="headings")
        for c in cols:
            self.sales_tree.heading(c, text=c)
            self.sales_tree.column(c, width=150)
        self.sales_tree.place(x=10, y=10, width=520, height=520)

        # Right: cart and controls
        cart_frame = ttk.LabelFrame(frame, text="Cart")
        cart_frame.place(x=540, y=10, width=540, height=520)

        cart_cols = ("ID", "Nombre", "Cantidad", "Unit Price", "Subtotal")
        self.cart_tree = ttk.Treeview(cart_frame, columns=cart_cols, show="headings")
        for c in cart_cols:
            self.cart_tree.heading(c, text=c)
            self.cart_tree.column(c, width=100)
        self.cart_tree.place(x=10, y=10, width=510, height=300)

        ttk.Button(cart_frame, text="Add Selected to Cart", command=self.add_selected_to_cart).place(x=10, y=320)
        ttk.Button(cart_frame, text="Remove Selected from Cart", command=self.remove_selected_from_cart).place(x=160, y=320)
        ttk.Button(cart_frame, text="Clear Cart", command=self.clear_cart).place(x=360, y=320)

        ttk.Label(cart_frame, text="Total:").place(x=10, y=360)
        self.total_var = tk.StringVar(value="0.00")
        ttk.Label(cart_frame, textvariable=self.total_var).place(x=60, y=360)

        ttk.Button(cart_frame, text="Finalize Sale", command=self.finalize_sale).place(x=10, y=400)
        ttk.Button(cart_frame, text="Refresh Products", command=self.load_products_for_sales).place(x=140, y=400)

        self.load_products_for_sales()

    def load_products_for_sales(self):
        for i in self.sales_tree.get_children():
            self.sales_tree.delete(i)
        rows = self.db.get_all_products()
        for r in rows:
            # r: id, nombre, precio_compra, precio_venta, cantidad, sku
            self.sales_tree.insert("", tk.END, values=(r[0], r[1], r[3], r[4]))

    def add_selected_to_cart(self):
        sel = self.sales_tree.selection()
        if not sel:
            messagebox.showwarning("Select", "Seleccione un producto")
            return
        item = self.sales_tree.item(sel[0])['values']
        idp, nombre, precio_venta, stock = item
        try:
            qty = simpledialog.askinteger("Cantidad", f"Ingrese cantidad para '{nombre}' (disponible {stock}):", minvalue=1, initialvalue=1)
            if qty is None:
                return
            if qty > stock:
                messagebox.showerror("Stock", "Cantidad mayor al stock disponible")
                return
            # Check if already in cart
            for c in self.cart:
                if c['id_producto'] == idp:
                    c['cantidad'] += qty
                    c['subtotal'] = Decimal(str(c['precio_unitario'])) * Decimal(c['cantidad'])
                    self.refresh_cart_view()
                    return
            price = Decimal(str(precio_venta))
            subtotal = price * Decimal(qty)
            self.cart.append({
                'id_producto': idp,
                'nombre': nombre,
                'cantidad': qty,
                'precio_unitario': price,
                'subtotal': subtotal
            })
            self.refresh_cart_view()
        except Exception as e:
            messagebox.showerror("Error", f"Entrada inválida: {e}")

    def refresh_cart_view(self):
        for i in self.cart_tree.get_children():
            self.cart_tree.delete(i)
        total = Decimal('0.00')
        for c in self.cart:
            self.cart_tree.insert("", tk.END, values=(c['id_producto'], c['nombre'], c['cantidad'], f"{c['precio_unitario']:.2f}", f"{c['subtotal']:.2f}"))
            total += c['subtotal']
        self.total_var.set(f"{total:.2f}")

    def remove_selected_from_cart(self):
        sel = self.cart_tree.selection()
        if not sel:
            messagebox.showwarning("Select", "Seleccione un item del carrito")
            return
        item = self.cart_tree.item(sel[0])['values']
        idp = item[0]
        self.cart = [c for c in self.cart if c['id_producto'] != idp]
        self.refresh_cart_view()

    def clear_cart(self):
        self.cart = []
        self.refresh_cart_view()

    def finalize_sale(self):
        if not self.cart:
            messagebox.showwarning("Cart", "El carrito está vacío")
            return
        # Prepare items for DB
        items = []
        for c in self.cart:
            items.append({
                'id_producto': c['id_producto'],
                'cantidad': c['cantidad'],
                'precio_unitario': str(c['precio_unitario'])
            })
        sale_id = self.db.create_sale(items)
        if sale_id:
            messagebox.showinfo("Venta", f"Venta realizada. ID: {sale_id}")
            self.clear_cart()
            self.load_products_for_sales()
            self.load_products()
            # Optionally refresh reports
            self.load_sales_report()

    # ---------------------------
    # Reports Tab
    # ---------------------------
    def build_reports_tab(self):
        frame = self.tab_reports

        # Sales report
        sales_frame = ttk.LabelFrame(frame, text="Sales")
        sales_frame.place(x=10, y=10, width=1060, height=300)

        cols = ("Sale ID", "Date", "Total", "Detail ID", "Product ID", "Product", "Qty", "Unit Price", "Subtotal")
        self.report_sales_tree = ttk.Treeview(sales_frame, columns=cols, show="headings")
        for c in cols:
            self.report_sales_tree.heading(c, text=c)
            self.report_sales_tree.column(c, width=110)
        self.report_sales_tree.place(x=10, y=10, width=1030, height=250)

        ttk.Button(sales_frame, text="Refresh Sales", command=self.load_sales_report).place(x=10, y=265)

        # Inventory report
        inv_frame = ttk.LabelFrame(frame, text="Inventory")
        inv_frame.place(x=10, y=320, width=1060, height=300)

        inv_cols = ("ID", "Product", "Category", "Purchase", "Sale", "Quantity", "SKU")
        self.report_inv_tree = ttk.Treeview(inv_frame, columns=inv_cols, show="headings")
        for c in inv_cols:
            self.report_inv_tree.heading(c, text=c)
            self.report_inv_tree.column(c, width=140)
        self.report_inv_tree.place(x=10, y=10, width=1030, height=240)

        ttk.Button(inv_frame, text="Refresh Inventory", command=self.load_inventory_report).place(x=10, y=255)

        # Initial load
        self.load_sales_report()
        self.load_inventory_report()

    def load_sales_report(self):
        for i in self.report_sales_tree.get_children():
            self.report_sales_tree.delete(i)
        rows = self.db.get_sales()
        for r in rows:
            # r: id_venta, fecha, total, id_detalle, id_producto, nombre, cantidad, precio_unitario, subtotal
            self.report_sales_tree.insert("", tk.END, values=r)

    def load_inventory_report(self):
        for i in self.report_inv_tree.get_children():
            self.report_inv_tree.delete(i)
        rows = self.db.get_all_products()
        for r in rows:
            # r: id, nombre, precio_compra, precio_venta, cantidad, sku
            self.report_inv_tree.insert("", tk.END, values=(r[0], r[1], "", f"{r[2]:.2f}", f"{r[3]:.2f}", r[4], r[5]))

# ---------------------------
# Run application
# ---------------------------
def main():
    cfg = DBConfig()
    db = DBHandler(cfg)
    root = tk.Tk()
    app = POSApp(root, db)
    root.mainloop()

if __name__ == "__main__":
    main()
